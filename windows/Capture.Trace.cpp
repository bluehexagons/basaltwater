// Owns one uniquely named, process-filtered PresentMon session. No elevation or
// changes to other ETW sessions. PresentMon also has its own finite timed stop.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <evntrace.h>
#include <iostream>
#include <string>
#include <vector>
#include <stdexcept>
#include <climits>
struct Handle {
    HANDLE value = nullptr;
    explicit Handle(HANDLE h = nullptr) : value(h) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};
static void require(bool value, char const* message) { if (!value) throw std::runtime_error(message); }
static long long number(wchar_t const* s, long long low, long long high) {
    wchar_t* end; errno = 0; auto n = wcstoll(s, &end, 10);
    require(*s && !*end && !errno && n >= low && n <= high, "invalid_number"); return n;
}
static long long created(HANDLE p) {
    FILETIME c, e, k, u; require(GetProcessTimes(p, &c, &e, &k, &u), "identity_unavailable");
    return (static_cast<long long>(c.dwHighDateTime) << 32) | c.dwLowDateTime;
}
static void admit(HANDLE target, long long identity, std::wstring const& name) {
    require(target && created(target) == identity && WaitForSingleObject(target, 0) == WAIT_TIMEOUT,
            "target_exited_or_replaced");
    Handle job(OpenJobObjectW(JOB_OBJECT_QUERY, FALSE, name.c_str())); BOOL member = FALSE;
    require(job.value && IsProcessInJob(target, job.value, &member) && member, "target_job_mismatch");
    // The wrapper validates account/session before dispatch; also verify the
    // pinned token here, without retaining the supervisor's kill-on-close Job.
    Handle token; require(OpenProcessToken(target, TOKEN_QUERY, &token.value), "owner_unavailable");
    Handle self; require(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &self.value), "owner_unavailable");
    BYTE a[4096], b[4096]; DWORD size;
    require(GetTokenInformation(token.value, TokenUser, a, sizeof(a), &size) &&
            GetTokenInformation(self.value, TokenUser, b, sizeof(b), &size) &&
            EqualSid(reinterpret_cast<TOKEN_USER*>(a)->User.Sid, reinterpret_cast<TOKEN_USER*>(b)->User.Sid), "owner_mismatch");
    DWORD session, ownSession;
    require(GetTokenInformation(token.value, TokenSessionId, &session, sizeof(session), &size) &&
            GetTokenInformation(self.value, TokenSessionId, &ownSession, sizeof(ownSession), &size) &&
            session > 0 && session == ownSession, "session_mismatch");
}
static std::wstring quote(std::wstring const& value) {
    std::wstring out = L"\""; size_t slashes = 0;
    for (wchar_t c : value) {
        if (c == L'\\') { ++slashes; continue; }
        out.append(c == L'"' ? slashes * 2 + 1 : slashes, L'\\'); out += c; slashes = 0;
    }
    out.append(slashes * 2, L'\\'); return out + L'"';
}
static unsigned long long length(std::wstring const& path) {
    WIN32_FILE_ATTRIBUTE_DATA info;
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &info)) return 0;
    return (static_cast<unsigned long long>(info.nFileSizeHigh) << 32) | info.nFileSizeLow;
}
static ULONG stop(std::wstring const& name) {
    std::vector<BYTE> buffer(sizeof(EVENT_TRACE_PROPERTIES) + 2048);
    auto props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(buffer.data());
    props->Wnode.BufferSize = static_cast<ULONG>(buffer.size());
    props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
    return ControlTraceW(0, name.c_str(), props, EVENT_TRACE_CONTROL_STOP);
}
int wmain(int argc, wchar_t** argv) {
    if (argc == 3 && std::wstring(argv[1]) == L"--cleanup") {
        std::wstring name = argv[2];
        std::wstring prefix = L"BasaltwaterTrace-";
        if (name.size() != prefix.size() + 32 || name.substr(0, prefix.size()) != prefix) return 2;
        for (size_t i = prefix.size(); i < name.size(); ++i)
            if (!((name[i] >= L'0' && name[i] <= L'9') || (name[i] >= L'a' && name[i] <= L'f'))) return 2;
        auto code = stop(name);
        bool clean = code == ERROR_SUCCESS || code == ERROR_WMI_INSTANCE_NOT_FOUND;
        std::cout << "{\"schema_version\":1,\"cleanup_confirmed\":" << (clean ? "true" : "false")
                  << ",\"stop_status\":" << code << "}\n"; return clean ? 0 : 1;
    }
    bool clean = false; std::string failure; ULONG stopStatus = ERROR_SUCCESS; DWORD code = 1;
    std::wstring name; Handle child, thread;
    try {
        require(argc == 10, "Expected PresentMon PID creation callerPID callerCreation job directory seconds session");
        auto pid = static_cast<DWORD>(number(argv[2], 1, MAXDWORD)); auto identity = number(argv[3], 1, LLONG_MAX);
        auto callerId = static_cast<DWORD>(number(argv[4], 1, MAXDWORD)); auto callerCreated = number(argv[5], 1, LLONG_MAX);
        std::wstring job = argv[6], directory = argv[7]; auto seconds = number(argv[8], 1, 30); name = argv[9];
        std::wstring prefix = L"BasaltwaterTrace-";
        require(name.size() == prefix.size() + 32 && name.substr(0, prefix.size()) == prefix, "invalid_session_name");
        for (size_t i = prefix.size(); i < name.size(); ++i)
            require((name[i] >= L'0' && name[i] <= L'9') || (name[i] >= L'a' && name[i] <= L'f'), "invalid_session_name");
        Handle target(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid));
        Handle caller(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, callerId));
        require(caller.value && created(caller.value) == callerCreated && WaitForSingleObject(caller.value, 0) == WAIT_TIMEOUT, "caller_identity");
        admit(target.value, identity, job);
        SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
        Handle log(CreateFileW((directory + L"\\presentmon.log").c_str(), GENERIC_WRITE, FILE_SHARE_READ, &security, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
        Handle nul(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &security, OPEN_EXISTING, 0, nullptr));
        require(log.value != INVALID_HANDLE_VALUE && nul.value != INVALID_HANDLE_VALUE, "trace_log_unavailable");
        std::wstring command = quote(argv[1]) + L" --process_id " + std::to_wstring(pid) +
            L" --output_file " + quote(directory + L"\\raw.csv") + L" --qpc_time --v2_metrics --timed " +
            std::to_wstring(seconds) + L" --terminate_after_timed --terminate_on_proc_exit --no_console_stats --no_track_input --session_name " + quote(name);
        STARTUPINFOW startup{}; startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdInput = nul.value; startup.hStdOutput = log.value; startup.hStdError = log.value;
        PROCESS_INFORMATION process{};
        require(CreateProcessW(argv[1], &command[0], nullptr, nullptr, TRUE, CREATE_NO_WINDOW,
            nullptr, directory.c_str(), &startup, &process), "presentmon_launch_failed");
        child.value = process.hProcess; thread.value = process.hThread;
        // The child's --timed stop remains independent if this helper is killed.
        // A hard failure retains the session name for exact explicit cleanup.
        auto start = GetTickCount64();
        while (WaitForSingleObject(child.value, 50) == WAIT_TIMEOUT) {
            if (WaitForSingleObject(caller.value, 0) != WAIT_TIMEOUT) { failure = "caller_exited"; break; }
            admit(target.value, identity, job);
            if (GetTickCount64() - start > static_cast<ULONGLONG>((seconds + 5) * 1000)) { failure = "trace_deadline"; break; }
            if (length(directory + L"\\raw.csv") > 64 * 1024 * 1024 || length(directory + L"\\presentmon.log") > 2 * 1024 * 1024) { failure = "trace_output_limit"; break; }
        }
        if (WaitForSingleObject(child.value, 0) == WAIT_TIMEOUT) {
            stopStatus = stop(name); // Stop this session so PresentMon can flush normally.
            if (WaitForSingleObject(child.value, 2000) != WAIT_OBJECT_0) {
                TerminateProcess(child.value, 125);
                require(WaitForSingleObject(child.value, 2000) == WAIT_OBJECT_0, "presentmon_cleanup_uncertain");
            }
        }
        require(GetExitCodeProcess(child.value, &code), "trace_exit_unavailable");
        if (code != 0 && failure.empty()) failure = "presentmon_failed";
    } catch (std::exception const& e) { failure = e.what(); }
    if (!name.empty()) {
        stopStatus = stop(name);
        clean = stopStatus == ERROR_SUCCESS || stopStatus == ERROR_WMI_INSTANCE_NOT_FOUND;
    }
    if (child.value && WaitForSingleObject(child.value, 0) == WAIT_TIMEOUT) {
        TerminateProcess(child.value, 125);
        clean = clean && WaitForSingleObject(child.value, 2000) == WAIT_OBJECT_0;
    }
    std::cout << "{\"schema_version\":1,\"complete\":" << (failure.empty() && clean ? "true" : "false")
        << ",\"cleanup_confirmed\":" << (clean ? "true" : "false") << ",\"exit_code\":" << code
        << ",\"stop_status\":" << stopStatus << ",\"error\":";
    if (failure.empty()) std::cout << "null"; else std::cout << '"' << failure << '"';
    std::cout << "}\n"; return failure.empty() && clean ? 0 : 1;
}
