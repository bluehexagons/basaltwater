// Opt-in synthetic D3D11 fixture. Never launched by the offline/native suite.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <wrl/client.h>
#include <fstream>
#include <iostream>
#include <string>
using Microsoft::WRL::ComPtr;
static LRESULT CALLBACK message(HWND hwnd, UINT id, WPARAM w, LPARAM l) {
    if (id == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(hwnd, id, w, l);
}
static std::wstring environment(wchar_t const* name) {
    wchar_t value[32768] = {};
    DWORD size = GetEnvironmentVariableW(name, value, _countof(value));
    if (size >= _countof(value)) throw std::runtime_error("Fixture environment too large");
    return value;
}
static void check(HRESULT result) { if (FAILED(result)) throw std::runtime_error("Fixture D3D call failed"); }
int wmain(int argc, wchar_t** argv) {
    try {
        bool hitch = argc == 2 && std::wstring(argv[1]) == L"--hitch";
        if (argc != 1 && !hitch) throw std::runtime_error("Expected optional --hitch");
        auto profile = environment(L"BASALTWATER_TEST_PROFILE");
        auto evidence = environment(L"BASALTWATER_TEST_EVIDENCE");
        auto ready = environment(L"BASALTWATER_TEST_READY_FILE");
        auto token = environment(L"BASALTWATER_TEST_READY_TOKEN");
        if (profile.empty() || evidence.empty() || ready.empty() || token.size() != 32)
            throw std::runtime_error("Fixture requires a disposable Basaltwater test session");
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        WNDCLASSW klass = {}; klass.lpfnWndProc = message;
        klass.hInstance = GetModuleHandleW(nullptr); klass.lpszClassName = L"BasaltwaterGpuFixture";
        if (!RegisterClassW(&klass)) throw std::runtime_error("Fixture class failed");
        HWND hwnd = CreateWindowW(klass.lpszClassName, L"Basaltwater disposable GPU fixture",
            WS_OVERLAPPEDWINDOW, 80, 80, 660, 420, nullptr, nullptr, klass.hInstance, nullptr);
        if (!hwnd) throw std::runtime_error("Fixture window failed");
        DXGI_SWAP_CHAIN_DESC description = {};
        description.BufferDesc.Width = 640; description.BufferDesc.Height = 360;
        description.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        description.SampleDesc.Count = 1; description.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        description.BufferCount = 2; description.OutputWindow = hwnd; description.Windowed = TRUE;
        description.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
        ComPtr<ID3D11Device> device; ComPtr<ID3D11DeviceContext> context; ComPtr<IDXGISwapChain> swap;
        check(D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
            nullptr, 0, D3D11_SDK_VERSION, &description, &swap, &device, nullptr, &context));
        ComPtr<ID3D11Texture2D> back; check(swap->GetBuffer(0, IID_PPV_ARGS(&back)));
        ComPtr<ID3D11RenderTargetView> target;
        check(device->CreateRenderTargetView(back.Get(), nullptr, &target));
        std::ofstream settings(profile + L"\\gpu-fixture.json");
        settings << "{\"disposable_profile_honored\":true}"; settings.close();
        std::ofstream log(evidence + L"\\gpu-events.jsonl", std::ios::binary);
        ShowWindow(hwnd, SW_SHOW);
        LARGE_INTEGER frequency; QueryPerformanceFrequency(&frequency);
        auto start = GetTickCount64(); int frames = 0; bool published = false, running = true;
        while (running && GetTickCount64() - start < 90000) {
            MSG msg;
            while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
                if (msg.message == WM_QUIT) running = false;
                TranslateMessage(&msg); DispatchMessageW(&msg);
            }
            if (!running) break;
            // Deliberately slow alternation makes independent PNG pixel checks decisive.
            bool blue = ((GetTickCount64() - start) / 500) % 2 != 0;
            float color[] = {blue ? 0.0f : 1.0f, blue ? 0.0f : 0.5f, blue ? 1.0f : 0.0f, 1.0f};
            context->ClearRenderTargetView(target.Get(), color);
            check(swap->Present(1, 0)); ++frames;
            if (!published && frames >= 5) {
                std::ofstream info(evidence + L"\\window.json");
                info << "{\"window\":" << reinterpret_cast<UINT_PTR>(hwnd)
                     << ",\"pid\":" << GetCurrentProcessId() << ",\"presented\":" << frames << "}";
                info.close();
                std::ofstream marker(ready + L".tmp", std::ios::binary);
                marker << "{\"Token\":\"" << std::string(token.begin(), token.end()) << "\"}";
                marker.close();
                if (!MoveFileW((ready + L".tmp").c_str(), ready.c_str()))
                    throw std::runtime_error("Fixture readiness publication failed");
                published = true;
            }
            if (frames % 30 == 0) {
                LARGE_INTEGER now; QueryPerformanceCounter(&now);
                log << "{\"sequence\":" << frames / 30 << ",\"presented\":" << frames
                    << ",\"qpc\":" << now.QuadPart << ",\"qpc_frequency\":" << frequency.QuadPart
                    << ",\"blue\":" << (blue ? "true" : "false") << "}\n"; log.flush();
            }
            if (hitch && frames % 60 == 0) Sleep(200);
        }
        if (IsWindow(hwnd)) DestroyWindow(hwnd);
        std::cout << "{\"rendered_frames\":" << frames << ",\"normal_exit\":true}\n";
        return 0;
    } catch (std::exception const& error) { std::cerr << error.what() << '\n'; return 1; }
}
