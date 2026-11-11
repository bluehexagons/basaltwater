// Process-tree loopback only. No endpoint activation or system-loopback fallback.
#define NOMINMAX
#include <windows.h>
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <mmdeviceapi.h>
#include <wrl.h>
#include <wrl/implements.h>
#include <winternl.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
#include <thread>
#include <stdexcept>
using namespace Microsoft::WRL;
struct Handle {
    HANDLE h = nullptr;
    explicit Handle(HANDLE value = nullptr) : h(value) {}
    ~Handle() { if (h && h != INVALID_HANDLE_VALUE) CloseHandle(h); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};
void require(bool value, const char* reason) { if (!value) throw std::runtime_error(reason); }
void check(HRESULT hr) { if (FAILED(hr)) throw hr; }
uint64_t created(HANDLE p) {
    FILETIME c{}, e{}, k{}, u{};
    require(GetProcessTimes(p, &c, &e, &k, &u), "identity_unavailable");
    return (uint64_t(c.dwHighDateTime) << 32) | c.dwLowDateTime;
}
uint64_t utc() { FILETIME f; GetSystemTimePreciseAsFileTime(&f); return (uint64_t(f.dwHighDateTime)<<32)|f.dwLowDateTime; }
int64_t qpc() { LARGE_INTEGER v; QueryPerformanceCounter(&v); return v.QuadPart; }
std::vector<BYTE> tokenInfo(HANDLE p, TOKEN_INFORMATION_CLASS kind) {
    HANDLE raw = nullptr; require(OpenProcessToken(p, TOKEN_QUERY, &raw), "token_unavailable"); Handle token(raw);
    DWORD bytes = 0; GetTokenInformation(token.h, kind, nullptr, 0, &bytes);
    require(bytes > 0 && bytes <= 65536, "token_size"); std::vector<BYTE> data(bytes);
    require(GetTokenInformation(token.h, kind, data.data(), bytes, &bytes), "token_unavailable"); return data;
}
void admit(HANDLE p, uint64_t identity, const std::wstring& jobName) {
    require(WaitForSingleObject(p, 0) == WAIT_TIMEOUT && created(p) == identity, "stale_identity");
    auto owner = tokenInfo(p, TokenUser), self = tokenInfo(GetCurrentProcess(), TokenUser);
    require(EqualSid(reinterpret_cast<TOKEN_USER*>(owner.data())->User.Sid,
                     reinterpret_cast<TOKEN_USER*>(self.data())->User.Sid), "account_mismatch");
    auto session = tokenInfo(p, TokenSessionId), current = tokenInfo(GetCurrentProcess(), TokenSessionId);
    require(*reinterpret_cast<DWORD*>(session.data()) == *reinterpret_cast<DWORD*>(current.data()), "session_mismatch");
    // Close immediately: retaining a query handle must not prolong kill-on-close.
    Handle job(OpenJobObjectW(JOB_OBJECT_QUERY, FALSE, jobName.c_str())); BOOL member = FALSE;
    require(job.h && IsProcessInJob(p, job.h, &member) && member, "job_membership_mismatch");
}
struct Activation : RuntimeClass<RuntimeClassFlags<ClassicCom>, FtmBase, IActivateAudioInterfaceCompletionHandler> {
    Handle ready{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    HRESULT result = E_PENDING; ComPtr<IAudioClient> client;
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation* operation) override {
        ComPtr<IUnknown> unknown; HRESULT activated = E_FAIL;
        result = operation->GetActivateResult(&activated, &unknown);
        if (SUCCEEDED(result)) result = activated;
        if (SUCCEEDED(result)) result = unknown.As(&client);
        SetEvent(ready.h); return S_OK;
    }
};
struct Summary {
    uint64_t silent = 0; int peak = 0;
    void add(const int16_t* data, size_t samples) {
        for (size_t i=0; i<samples; ++i) { int v=std::abs(int(data[i])); peak=std::max(peak,v); if(v==0) ++silent; }
    }
};
size_t appendPcm(std::vector<int16_t>& pcm, size_t maxFrames, const BYTE* data,
                 size_t frames, bool silent, Summary& summary) {
    const size_t take=std::min(frames,maxFrames-pcm.size()/2), offset=pcm.size();
    pcm.resize(offset+take*2);
    if(silent) std::fill(pcm.begin()+offset,pcm.end(),int16_t(0));
    else if(take) memcpy(pcm.data()+offset,data,take*4);
    summary.add(pcm.data()+offset,take*2);
    return frames-take;
}
uint64_t number(const wchar_t* s) {
    require(s && *s, "invalid_number"); for(auto c= s; *c; ++c) require(*c>=L'0' && *c<=L'9', "invalid_number");
    return std::stoull(s);
}
void writeAll(HANDLE f, const void* data, DWORD size) { DWORD written=0; require(WriteFile(f,data,size,&written,nullptr) && written==size,"disk_write_failed"); }
void wav(const wchar_t* path, const std::vector<int16_t>& pcm) {
    Handle file(CreateFileW(path, GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
    require(file.h != INVALID_HANDLE_VALUE,"output_exists_or_unavailable");
    DWORD bytes=DWORD(pcm.size()*2);
    DWORD header[] = {0x46464952,36+bytes,0x45564157,0x20746d66,16,0x00020001,44100,176400,0x00100004,0x61746164,bytes};
    writeAll(file.h,header,sizeof(header)); if(bytes) writeAll(file.h,pcm.data(),bytes);
    require(FlushFileBuffers(file.h),"disk_flush_failed");
}
int wmain(int argc, wchar_t** argv) {
    if((argc==2 || argc==3) && std::wstring(argv[1])==L"--self-test") {
        Summary s; int16_t fixture[]={0,-32768,32767,0};
        std::vector<int16_t> pcm; pcm.reserve(4);
        require(appendPcm(pcm,2,reinterpret_cast<BYTE*>(fixture),2,false,s)==0,"append_test");
        require(appendPcm(pcm,2,nullptr,10,true,s)==10 && pcm.size()==4,"size_limit_test");
        require(s.peak==32768 && s.silent==2,"summary_test");
        if(argc==3) wav(argv[2],pcm);
        std::cout << "{\"ok\":true,\"offline\":true}\n"; return 0;
    }
    // pid, FILETIME, exact named Job, milliseconds, output, parent PID+FILETIME,
    // optional named cancellation event ('-' means none). Wrapper is authoritative admission.
    if(argc!=10 || (std::wstring(argv[1])!=L"--capture" && std::wstring(argv[1])!=L"--verify")) { std::cout << "{\"ok\":false,\"reason\":\"invalid_arguments\"}\n"; return 2; }
    try {
        auto pid=number(argv[2]), identity=number(argv[3]), ms=number(argv[5]);
        auto parentId=number(argv[7]), parentCreated=number(argv[8]);
        require(pid>0 && pid<=MAXDWORD && parentId>0 && parentId<=MAXDWORD && ms>=100 && ms<=30000,"bounds");
        std::wstring jobName=argv[4];
        require(jobName.size()==65 && jobName.substr(0,29)==L"Local\\BasaltwaterDevelopment-" && jobName.substr(61)==L"-job","invalid_job");
        for(size_t i=29;i<61;++i) require((jobName[i]>=L'0' && jobName[i]<=L'9') || (jobName[i]>=L'a' && jobName[i]<=L'f'),"invalid_job");
        Handle parent(OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,DWORD(parentId)));
        require(parent.h && created(parent.h)==parentCreated,"parent_identity");
        Handle cancel(std::wstring(argv[9])==L"-" ? nullptr : OpenEventW(SYNCHRONIZE,FALSE,argv[9]));
        require(std::wstring(argv[9])==L"-" || cancel.h,"cancel_unavailable");
        if(cancel.h && WaitForSingleObject(cancel.h,0)==WAIT_OBJECT_0) { std::cout << "{\"ok\":false,\"reason\":\"cancelled\"}\n"; return 125; }
        // Covers stuck activation, filesystem and COM calls as well as ordinary waits.
        // Detached thread exists only in this finite helper; it launches no children.
        auto parentHandle=parent.h, cancelHandle=cancel.h;
        std::thread([parentHandle]() {
            DWORD r=WaitForSingleObject(parentHandle,42000);
            TerminateProcess(GetCurrentProcess(),r==WAIT_TIMEOUT?124:125);
        }).detach();
        // Handles are deliberately valid until process exit, including on exceptions.
        parent.h=nullptr; cancel.h=nullptr;
        Handle target(OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,DWORD(pid)));
        require(target.h,"target_unavailable"); admit(target.h,identity,jobName);
        if(std::wstring(argv[1])==L"--verify") { std::cout << "{\"ok\":true,\"verified\":true}\n"; return 0; }
        RTL_OSVERSIONINFOW version{}; version.dwOSVersionInfoSize=sizeof(version);
        auto getVersion=reinterpret_cast<LONG(WINAPI*)(PRTL_OSVERSIONINFOW)>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"RtlGetVersion"));
        require(getVersion && getVersion(&version)==0,"os_version_unavailable");
        if(version.dwBuildNumber<20348) { std::cout << "{\"ok\":false,\"reason\":\"unsupported_os\",\"minimumBuild\":20348}\n"; return 3; }
        check(CoInitializeEx(nullptr,COINIT_MULTITHREADED));
        auto callback=Make<Activation>(); require(callback && callback->ready.h,"activation_event");
        AUDIOCLIENT_ACTIVATION_PARAMS params{}; params.ActivationType=AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
        params.ProcessLoopbackParams.TargetProcessId=DWORD(pid);
        params.ProcessLoopbackParams.ProcessLoopbackMode=PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
        PROPVARIANT prop{}; prop.vt=VT_BLOB; prop.blob.cbSize=sizeof(params); prop.blob.pBlobData=reinterpret_cast<BYTE*>(&params);
        ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
        check(ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,__uuidof(IAudioClient),&prop,callback.Get(),&operation));
        require(WaitForSingleObject(callback->ready.h,5000)==WAIT_OBJECT_0,"activation_timeout"); check(callback->result);
        auto client=callback->client;
        WAVEFORMATEX format{WAVE_FORMAT_PCM,2,44100,176400,4,16,0};
        check(client->Initialize(AUDCLNT_SHAREMODE_SHARED,AUDCLNT_STREAMFLAGS_LOOPBACK|AUDCLNT_STREAMFLAGS_EVENTCALLBACK|AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,0,0,&format,nullptr));
        UINT32 engineFrames=0; check(client->GetBufferSize(&engineFrames));
        require(engineFrames<=88200,"engine_buffer_exceeds_two_seconds");
        Handle samples(CreateEventW(nullptr,FALSE,FALSE,nullptr)); require(samples.h,"sample_event"); check(client->SetEventHandle(samples.h));
        ComPtr<IAudioCaptureClient> capture; check(client->GetService(IID_PPV_ARGS(&capture)));
        const size_t maxFrames=size_t(ms*44100/1000), maxSamples=maxFrames*2;
        std::vector<int16_t> pcm; pcm.reserve(maxSamples); Summary summary;
        uint64_t packets=0, discontinuities=0, timestampErrors=0, lostFrames=0, trimmedFrames=0;
        uint64_t firstPacketQpc=0,lastPacketQpc=0,firstDevice=0,lastDevice=0,expectedDevice=0; bool havePosition=false;
        LARGE_INTEGER frequency; QueryPerformanceFrequency(&frequency);
        admit(target.h,identity,jobName); auto startUtc=utc(); auto start=qpc(); check(client->Start());
        std::string reason="completed";
        try {
            while(reason=="completed" && double(qpc()-start)/frequency.QuadPart < double(ms)/1000 && pcm.size()<maxSamples) {
                admit(target.h,identity,jobName);
                HANDLE waits[]={samples.h,parentHandle,cancelHandle};
                DWORD r=WaitForMultipleObjects(cancelHandle?3:2,waits,FALSE,20);
                if(r==WAIT_OBJECT_0+1 || r==WAIT_OBJECT_0+2) { reason="cancelled"; break; }
                require(r!=WAIT_FAILED,"sample_wait");
                UINT32 available=0; check(capture->GetNextPacketSize(&available));
                while(available && pcm.size()<maxSamples) {
                    if(double(qpc()-start)/frequency.QuadPart>=double(ms)/1000) break;
                    if(cancelHandle && WaitForSingleObject(cancelHandle,0)==WAIT_OBJECT_0) { reason="cancelled"; break; }
                    admit(target.h,identity,jobName);
                    BYTE* data=nullptr; UINT32 frames=0; DWORD flags=0; UINT64 device=0,position=0;
                    check(capture->GetBuffer(&data,&frames,&flags,&device,&position));
                    // No throwing work between GetBuffer and ReleaseBuffer: reserved capacity.
                    trimmedFrames+=appendPcm(pcm,maxFrames,data,frames,(flags&AUDCLNT_BUFFERFLAGS_SILENT)!=0,summary);
                    ++packets;
                    if(flags&AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) ++discontinuities;
                    if(flags&AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR) ++timestampErrors;
                    else {
                        if(!havePosition) { firstPacketQpc=position; firstDevice=device; }
                        if(havePosition && device>expectedDevice) lostFrames+=device-expectedDevice;
                        expectedDevice=device+frames; havePosition=true; lastPacketQpc=position; lastDevice=device;
                    }
                    check(capture->ReleaseBuffer(frames));
                    check(capture->GetNextPacketSize(&available));
                }
            }
        } catch(HRESULT) { reason="audio_api_failed_during_capture"; }
          catch(const std::exception&) { reason="target_admission_lost"; }
        if(FAILED(client->Stop())) reason="audio_stop_failed";
        auto end=qpc(); auto endUtc=utc();
        wav(argv[6],pcm);
        std::cout << "{\"ok\":" << (reason=="completed"?"true":"false") << ",\"reason\":\"" << reason
          << "\",\"format\":{\"encoding\":\"PCM_S16LE\",\"bitsPerSample\":16,\"sampleRate\":44100,\"channels\":2,\"blockAlign\":4},\"requestedMilliseconds\":" << ms
          << ",\"pid\":" << pid << ",\"createdFileTimeUtc\":\"" << identity << "\",\"frames\":" << pcm.size()/2
          << ",\"wavBytes\":" << 44+pcm.size()*2 << ",\"silentSamples\":" << summary.silent << ",\"peakNormalized\":" << double(summary.peak)/32768
          << ",\"packets\":" << packets << ",\"discontinuities\":" << discontinuities << ",\"timestampErrors\":" << timestampErrors
          << ",\"inferredLostFrames\":" << lostFrames << ",\"trimmedFrames\":" << trimmedFrames
          << ",\"qpcFrequency\":" << frequency.QuadPart << ",\"startQpc\":" << start << ",\"endQpc\":" << end
          << ",\"startUtcFileTime\":\"" << startUtc << "\",\"endUtcFileTime\":\"" << endUtc
          << "\",\"firstPacketQpc100ns\":" << firstPacketQpc << ",\"lastPacketQpc100ns\":" << lastPacketQpc
          << ",\"firstDeviceFrame\":" << firstDevice << ",\"lastDeviceFrame\":" << lastDevice
          << ",\"packetPositionAvailable\":" << (havePosition?"true":"false")
          << ",\"endpointEvents\":\"unavailable_process_loopback_endpoint_independent\",\"lossCoverage\":\"flags_and_forward_device_gaps_only\"}\n";
        return reason=="completed"?0:125;
    } catch(HRESULT hr) { std::cout << "{\"ok\":false,\"reason\":\"audio_api_failed\",\"hresult\":" << uint32_t(hr) << "}\n"; }
      catch(const std::exception& e) { std::cout << "{\"ok\":false,\"reason\":\"" << e.what() << "\"}\n"; }
    return 1;
}
