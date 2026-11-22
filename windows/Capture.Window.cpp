// Bounded Windows Graphics Capture. Built with the optional Windows C++/SDK tools.
// This helper never selects a window, changes focus, or returns input authority.
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <dwmapi.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wincodec.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <atomic>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>
#include <thread>
#include <sstream>

using namespace winrt;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;

struct Handle {
    HANDLE value = nullptr;
    explicit Handle(HANDLE handle = nullptr) : value(handle) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    Handle(Handle const&) = delete;
    Handle& operator=(Handle const&) = delete;
};
static void require(bool value, char const* message) {
    if (!value) throw std::runtime_error(message);
}
static long long integer(wchar_t const* text, long long minimum, long long maximum) {
    wchar_t* end = nullptr;
    errno = 0;
    auto value = wcstoll(text, &end, 10);
    require(text[0] && end && !*end && !errno && value >= minimum && value <= maximum,
            "Invalid numeric capture argument");
    return value;
}
static long long qpc() {
    LARGE_INTEGER value;
    require(QueryPerformanceCounter(&value) != FALSE, "QPC unavailable");
    return value.QuadPart;
}
static long long creation(HANDLE process) {
    FILETIME created, exited, kernel, user;
    require(GetProcessTimes(process, &created, &exited, &kernel, &user) != FALSE,
            "Process creation identity unavailable");
    return (static_cast<long long>(created.dwHighDateTime) << 32) | created.dwLowDateTime;
}
static std::vector<unsigned char> owner(HANDLE process) {
    HANDLE raw = nullptr;
    require(OpenProcessToken(process, TOKEN_QUERY, &raw) != FALSE, "Process owner unavailable");
    Handle token(raw);
    DWORD size = 0;
    GetTokenInformation(token.value, TokenUser, nullptr, 0, &size);
    require(size > 0 && size < 65536, "Invalid owner token size");
    std::vector<unsigned char> data(size);
    require(GetTokenInformation(token.value, TokenUser, data.data(), size, &size) != FALSE,
            "Process owner unavailable");
    return data;
}
static void peer(HANDLE process, DWORD pid, long long created) {
    require(process && WaitForSingleObject(process, 0) == WAIT_TIMEOUT &&
            creation(process) == created, "Stale or exited capture peer");
    DWORD targetSession = 0, ownSession = 0;
    require(ProcessIdToSessionId(pid, &targetSession) &&
            ProcessIdToSessionId(GetCurrentProcessId(), &ownSession) &&
            ownSession != 0 && ownSession == targetSession, "Capture peer session mismatch");
    auto target = owner(process), self = owner(GetCurrentProcess());
    require(EqualSid(reinterpret_cast<TOKEN_USER*>(target.data())->User.Sid,
                     reinterpret_cast<TOKEN_USER*>(self.data())->User.Sid) != FALSE,
            "Capture peer account mismatch");
}
static void watchdog(HANDLE caller) {
    HANDLE duplicate = nullptr;
    require(DuplicateHandle(GetCurrentProcess(), caller, GetCurrentProcess(), &duplicate,
                            SYNCHRONIZE, FALSE, 0) != FALSE, "Caller watchdog unavailable");
    // The waiter owns a separate handle until process exit. The main thread
    // retains its query handle for repeated identity checks.
    std::thread([duplicate]() {
        auto status = WaitForSingleObject(duplicate, 20000);
        TerminateProcess(GetCurrentProcess(), status == WAIT_TIMEOUT ? 124 : 125);
    }).detach();
}
static void desktop() {
    auto h = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
    require(h != nullptr, "Interactive input desktop unavailable");
    wchar_t name[256] = {}; DWORD bytes = 0;
    bool available = GetUserObjectInformationW(h, UOI_NAME, name, sizeof(name), &bytes) != FALSE;
    CloseDesktop(h);
    require(available && std::wstring(name) == L"Default", "Locked or secure desktop unsupported");
}
static RECT window(HWND hwnd, DWORD pid, int width, int height, RECT const* before = nullptr) {
    desktop(); DWORD actual = 0; RECT rect;
    GetWindowThreadProcessId(hwnd, &actual);
    require(IsWindow(hwnd) && IsWindowVisible(hwnd) && !IsIconic(hwnd) && actual == pid,
            "Capture window closed, hidden, minimized or replaced");
    require(GetWindowRect(hwnd, &rect) != FALSE && rect.right - rect.left == width &&
            rect.bottom - rect.top == height, "Capture window geometry changed");
    if (before) require(EqualRect(&rect, before) != FALSE, "Capture window moved during acquisition");
    return rect;
}
static std::string display_snapshot(HWND hwnd) {
    MONITORINFOEXW monitor = {}; monitor.cbSize = sizeof(monitor);
    if (!GetMonitorInfoW(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), &monitor))
        return "{\"status\":\"unavailable\",\"hdr_active\":null}";
    UINT32 pathCount = 0, modeCount = 0;
    auto status = GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &pathCount, &modeCount);
    if (status != ERROR_SUCCESS || pathCount > 64 || modeCount > 256)
        return "{\"status\":\"unavailable\",\"hdr_active\":null}";
    std::vector<DISPLAYCONFIG_PATH_INFO> paths(pathCount);
    std::vector<DISPLAYCONFIG_MODE_INFO> modes(modeCount);
    status = QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, &pathCount, paths.data(), &modeCount, modes.data(), nullptr);
    if (status != ERROR_SUCCESS) return "{\"status\":\"changed-or-unavailable\",\"hdr_active\":null}";
    std::ostringstream out;
    out << "{\"status\":\"observed\",\"monitor_bounds\":[" << monitor.rcMonitor.left << ','
        << monitor.rcMonitor.top << ',' << monitor.rcMonitor.right << ',' << monitor.rcMonitor.bottom
        << "],\"targets\":[";
    bool first = true;
    for (UINT32 index = 0; index < pathCount; ++index) {
        auto const& path = paths[index];
        DISPLAYCONFIG_SOURCE_DEVICE_NAME source = {};
        source.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME; source.header.size = sizeof(source);
        source.header.adapterId = path.sourceInfo.adapterId; source.header.id = path.sourceInfo.id;
        if (DisplayConfigGetDeviceInfo(&source.header) != ERROR_SUCCESS ||
            wcscmp(source.viewGdiDeviceName, monitor.szDevice)) continue;
        if (!first) out << ','; first = false;
        out << "{\"refresh_numerator\":" << path.targetInfo.refreshRate.Numerator
            << ",\"refresh_denominator\":" << path.targetInfo.refreshRate.Denominator;
#if defined(NTDDI_WIN11_GA) && NTDDI_VERSION >= NTDDI_WIN11_GA
        DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO_2 color = {};
        color.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO_2;
        color.header.size = sizeof(color); color.header.adapterId = path.targetInfo.adapterId;
        color.header.id = path.targetInfo.id;
        auto colorStatus = DisplayConfigGetDeviceInfo(&color.header);
        if (colorStatus == ERROR_SUCCESS) {
            out << ",\"color_api\":\"advanced-color-info-2\",\"active_color_mode\":"
                << static_cast<int>(color.activeColorMode) << ",\"hdr_active\":"
                << (color.activeColorMode == DISPLAYCONFIG_ADVANCED_COLOR_MODE_HDR ? "true" : "false")
                << ",\"hdr_supported\":" << (color.highDynamicRangeSupported ? "true" : "false")
                << ",\"hdr_user_enabled\":" << (color.highDynamicRangeUserEnabled ? "true" : "false")
                << ",\"bits_per_channel\":" << color.bitsPerColorChannel;
        } else out << ",\"hdr_active\":null,\"color_query_status\":" << colorStatus;
#else
        out << ",\"hdr_active\":null,\"color_query_status\":\"newer-sdk-required\"";
#endif
        out << '}';
    }
    out << "]}"; return out.str();
}
static void save_png(ID3D11Device* device, ID3D11DeviceContext* context,
                     ID3D11Texture2D* texture, int width, int height,
                     std::wstring const& path, unsigned long long& total) {
    require(width > 0 && height > 0 && static_cast<long long>(width) * height <= 8388608,
            "Capture exceeds eight-megapixel budget");
    D3D11_TEXTURE2D_DESC description = {};
    texture->GetDesc(&description);
    require(description.Width >= static_cast<UINT>(width) && description.Height >= static_cast<UINT>(height),
            "Incomplete resized capture surface");
    description.Width = width; description.Height = height;
    description.BindFlags = 0; description.MiscFlags = 0;
    description.CPUAccessFlags = D3D11_CPU_ACCESS_READ; description.Usage = D3D11_USAGE_STAGING;
    com_ptr<ID3D11Texture2D> staging;
    check_hresult(device->CreateTexture2D(&description, nullptr, staging.put()));
    D3D11_BOX box = {0, 0, 0, static_cast<UINT>(width), static_cast<UINT>(height), 1};
    context->CopySubresourceRegion(staging.get(), 0, 0, 0, 0, texture, 0, &box);
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    check_hresult(context->Map(staging.get(), 0, D3D11_MAP_READ, 0, &mapped));
    try {
        // A fixed-capacity memory stream bounds compressed output before disk I/O.
        std::vector<BYTE> encoded(16 * 1024 * 1024);
        auto factory = create_instance<IWICImagingFactory>(CLSID_WICImagingFactory);
        com_ptr<IWICStream> stream; check_hresult(factory->CreateStream(stream.put()));
        check_hresult(stream->InitializeFromMemory(encoded.data(), static_cast<DWORD>(encoded.size())));
        com_ptr<IWICBitmapEncoder> encoder;
        check_hresult(factory->CreateEncoder(GUID_ContainerFormatPng, nullptr, encoder.put()));
        check_hresult(encoder->Initialize(stream.get(), WICBitmapEncoderNoCache));
        com_ptr<IWICBitmapFrameEncode> frame; com_ptr<IPropertyBag2> options;
        check_hresult(encoder->CreateNewFrame(frame.put(), options.put()));
        check_hresult(frame->Initialize(options.get()));
        check_hresult(frame->SetSize(width, height));
        WICPixelFormatGUID format = GUID_WICPixelFormat32bppBGRA;
        check_hresult(frame->SetPixelFormat(&format));
        require(IsEqualGUID(format, GUID_WICPixelFormat32bppBGRA), "Unsupported PNG pixel conversion");
        check_hresult(frame->WritePixels(height, mapped.RowPitch, mapped.RowPitch * height,
                                        static_cast<BYTE*>(mapped.pData)));
        check_hresult(frame->Commit()); check_hresult(encoder->Commit());
        LARGE_INTEGER zero = {}; ULARGE_INTEGER used = {};
        check_hresult(stream->Seek(zero, STREAM_SEEK_CUR, &used));
        require(used.QuadPart > 0 && used.QuadPart <= encoded.size() &&
                total + used.QuadPart <= 64 * 1024 * 1024, "Capture output byte budget exceeded");
        Handle file(CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                                FILE_ATTRIBUTE_NORMAL, nullptr));
        require(file.value != INVALID_HANDLE_VALUE, "Capture output already exists or is unavailable");
        DWORD written = 0;
        require(WriteFile(file.value, encoded.data(), static_cast<DWORD>(used.QuadPart), &written, nullptr) &&
                written == used.QuadPart && FlushFileBuffers(file.value), "PNG write incomplete");
        total += written;
    } catch (...) { context->Unmap(staging.get(), 0); throw; }
    context->Unmap(staging.get(), 0);
}
int wmain(int argc, wchar_t** argv) {
    if (argc == 2 && std::wstring(argv[1]) == L"--version") {
        std::cout << "Basaltwater WGC helper 1\n"; return 0;
    }
    int count = 0; unsigned long long bytes = 0;
    std::atomic<unsigned long> notifications{0};
    std::string failure;
    try {
        // Explicit fault probe: exercise the production watchdog against a
        // deliberately stalled main thread without touching COM or a GPU.
        if (argc == 4 && std::wstring(argv[1]) == L"--watchdog-test") {
            auto id = static_cast<DWORD>(integer(argv[2], 1, MAXDWORD));
            auto identity = integer(argv[3], 1, LLONG_MAX);
            Handle parent(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, id));
            peer(parent.value, id, identity); watchdog(parent.value);
            Sleep(INFINITE); return 1;
        }
        require(argc == 12, "Expected HWND PID creation callerPID callerCreation directory ms frames interval width height");
        auto hwnd = reinterpret_cast<HWND>(integer(argv[1], 1, LLONG_MAX));
        auto pid = static_cast<DWORD>(integer(argv[2], 1, MAXDWORD));
        auto created = integer(argv[3], 1, LLONG_MAX);
        auto callerId = static_cast<DWORD>(integer(argv[4], 1, MAXDWORD));
        auto callerCreated = integer(argv[5], 1, LLONG_MAX);
        std::wstring directory = argv[6];
        auto milliseconds = integer(argv[7], 100, 10000);
        auto maximum = integer(argv[8], 1, 120);
        auto interval = integer(argv[9], 16, 10000);
        auto width = static_cast<int>(integer(argv[10], 1, 16384));
        auto height = static_cast<int>(integer(argv[11], 1, 16384));
        require(static_cast<long long>(width) * height <= 8388608, "Capture pixel budget exceeded");
        Handle target(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid));
        Handle caller(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, callerId));
        peer(target.value, pid, created); peer(caller.value, callerId, callerCreated);
        // A stalled COM/GPU call or shutdown must not outlive the controller.
        watchdog(caller.value);
        init_apartment(apartment_type::multi_threaded);
        require(SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) != nullptr,
                "Per-monitor DPI context unavailable");
        window(hwnd, pid, width, height);
        require(GraphicsCaptureSession::IsSupported(), "Windows Graphics Capture unsupported");
        auto interop = get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        GraphicsCaptureItem item{nullptr};
        check_hresult(interop->CreateForWindow(hwnd, guid_of<GraphicsCaptureItem>(), put_abi(item)));
        com_ptr<ID3D11Device> device; com_ptr<ID3D11DeviceContext> context;
        check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, device.put(), nullptr, context.put()));
        com_ptr<IInspectable> inspectable;
        check_hresult(CreateDirect3D11DeviceFromDXGIDevice(device.as<IDXGIDevice>().get(), inspectable.put()));
        auto direct = inspectable.as<IDirect3DDevice>();
        auto pool = Direct3D11CaptureFramePool::CreateFreeThreaded(direct,
            DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, item.Size());
        Handle arrived(CreateEventW(nullptr, FALSE, FALSE, nullptr));
        require(arrived.value != nullptr, "Frame event unavailable");
        auto subscription = pool.FrameArrived(auto_revoke, [&](auto const&, auto const&) {
            notifications.fetch_add(1); SetEvent(arrived.value);
        });
        auto session = pool.CreateCaptureSession(item);
        session.IsCursorCaptureEnabled(false);
        std::ofstream journal(directory + L"\\frames.jsonl", std::ios::binary);
        require(journal.good(), "Frame journal unavailable");
        LARGE_INTEGER frequency; QueryPerformanceFrequency(&frequency);
        auto start = qpc(), next = start;
        auto deadline = start + milliseconds * frequency.QuadPart / 1000;
        session.StartCapture();
        try {
            while (count < maximum && qpc() < deadline) {
                peer(target.value, pid, created); peer(caller.value, callerId, callerCreated);
                auto bounds = window(hwnd, pid, width, height);
                auto remaining = static_cast<DWORD>(std::max(1LL, std::min(100LL,
                    (deadline - qpc()) * 1000 / frequency.QuadPart)));
                WaitForSingleObject(arrived.value, remaining);
                auto frame = pool.TryGetNextFrame();
                if (!frame || qpc() < next) continue;
                auto acquired = qpc(); auto size = frame.ContentSize();
                RECT imageBounds = {};
                check_hresult(DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, &imageBounds, sizeof(imageBounds)));
                require(imageBounds.right - imageBounds.left == size.Width &&
                        imageBounds.bottom - imageBounds.top == size.Height,
                        "GPU frame and desktop content rectangle differ");
                auto access = frame.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
                com_ptr<ID3D11Texture2D> texture;
                check_hresult(access->GetInterface(guid_of<ID3D11Texture2D>(), texture.put_void()));
                wchar_t name[40]; swprintf_s(name, L"frame-%04d.png", count);
                save_png(device.get(), context.get(), texture.get(), size.Width, size.Height,
                         directory + L"\\" + name, bytes);
                window(hwnd, pid, width, height, &bounds);
                RECT client = {}; POINT origin = {};
                require(GetClientRect(hwnd, &client) && ClientToScreen(hwnd, &origin), "Client geometry unavailable");
                journal << "{\"sequence\":" << count << ",\"artifact\":\"frame-"
                    << std::setw(4) << std::setfill('0') << count << ".png\",\"frame_time_100ns\":"
                    << frame.SystemRelativeTime().count() << ",\"acquired_qpc\":" << acquired
                    << ",\"qpc_frequency\":" << frequency.QuadPart
                    << ",\"width\":" << size.Width << ",\"height\":" << size.Height
                    << ",\"window_origin\":[" << bounds.left << ',' << bounds.top
                    << "],\"image_origin_screen\":[" << imageBounds.left << ',' << imageBounds.top
                    << "],\"client_origin\":[" << origin.x << ',' << origin.y
                    << "],\"client_offset\":[" << origin.x - imageBounds.left << ',' << origin.y - imageBounds.top
                    << "],\"client_geometry\":[" << client.right << ',' << client.bottom
                    << "],\"dpi\":" << GetDpiForWindow(hwnd)
                    << ",\"display\":" << display_snapshot(hwnd) << "}\n";
                journal.flush(); require(journal.good(), "Frame journal write failed");
                ++count; next = acquired + interval * frequency.QuadPart / 1000;
            }
        } catch (...) { session.Close(); pool.Close(); throw; }
        session.Close(); subscription.revoke(); pool.Close();
        require(count > 0, "No GPU frame arrived before capture deadline");
    } catch (hresult_error const& e) { failure = "Windows capture HRESULT " + std::to_string(e.code().value); }
      catch (std::exception const& e) { failure = e.what(); }
    std::cout << "{\"schema_version\":1,\"backend\":\"windows-graphics-capture\",\"complete\":"
        << (failure.empty() ? "true" : "false") << ",\"frames\":" << count
        << ",\"bytes\":" << bytes << ",\"notifications\":" << notifications.load()
        << ",\"source_frame_loss\":null,\"error\":";
    if (failure.empty()) std::cout << "null";
    else std::cout << '"' << failure << '"'; // Errors above contain no user-supplied strings.
    std::cout << "}\n";
    return failure.empty() ? 0 : 1;
}
