<# Bounded stream draining and a thread-scoped idle-sleep request. No power-plan changes. #>
function Import-DevelopmentProcessType {
    if ('Basaltwater.DevelopmentStreams' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Runtime.InteropServices;
namespace Basaltwater {
    // Create the native executable in the guardian's job atomically. Packaged
    // PowerShell cannot be assumed to pass Job Object membership to children.
    // https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812/
    public static class DevelopmentProcess {
        [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct Startup {
            public int Size; public string Reserved, Desktop, Title;
            public uint X, Y, Width, Height, CharsX, CharsY, Fill, Flags;
            public ushort ShowWindow, ReservedSize;
            public IntPtr ReservedBytes, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct StartupEx { public Startup Startup; public IntPtr Attributes; }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr Process, Thread; public uint Id, ThreadId; }
        [StructLayout(LayoutKind.Sequential)] struct SecurityAttributes {
            public int Size; public IntPtr Descriptor; [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr OpenJobObject(uint access, bool inherit, string name);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, uint flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern bool CreateProcess(string exe, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity,
            bool inherit, uint flags, IntPtr environment, string directory, ref StartupEx startup, out ProcessInfo process);
        [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int kind);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateFile(string path, uint access, uint share, ref SecurityAttributes security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        static Exception Error() { return new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error()); }
        static string Quote(string value) {
            if (value.IndexOf('\0') >= 0) throw new ArgumentException("Arguments cannot contain NUL");
            var result = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') result.Append('\\', slashes * 2 + 1);
                else result.Append('\\', slashes);
                result.Append(c); slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }
        static IntPtr CopyStandardHandle(int kind) {
            IntPtr result;
            if (!DuplicateHandle(GetCurrentProcess(), GetStdHandle(kind), GetCurrentProcess(), out result, 0, true, 2)) throw Error();
            return result;
        }
        public static int Run(string jobName, string exe, string[] args, string directory) {
            IntPtr job = IntPtr.Zero, list = IntPtr.Zero, values = IntPtr.Zero;
            IntPtr input = IntPtr.Zero, output = IntPtr.Zero, error = IntPtr.Zero;
            var process = new ProcessInfo(); bool initialized = false, exited = false;
            try {
                var command = new StringBuilder(Quote(exe));
                foreach (string arg in args) command.Append(' ').Append(Quote(arg));
                if (command.Length >= 32767) throw new ArgumentException("Windows command line exceeds 32766 characters");
                job = OpenJobObject(1, false, jobName); // JOB_OBJECT_ASSIGN_PROCESS; never inherited.
                if (job == IntPtr.Zero) throw Error();
                output = CopyStandardHandle(-11); error = CopyStandardHandle(-12);
                var security = new SecurityAttributes { Size = Marshal.SizeOf(typeof(SecurityAttributes)), Inherit = true };
                input = CreateFile("NUL", 0x80000000, 3, ref security, 3, 0, IntPtr.Zero);
                if (input == new IntPtr(-1)) { input = IntPtr.Zero; throw Error(); }
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);
                list = Marshal.AllocHGlobal(size);
                if (!InitializeProcThreadAttributeList(list, 2, 0, ref size)) throw Error();
                initialized = true;
                values = Marshal.AllocHGlobal(4 * IntPtr.Size);
                Marshal.WriteIntPtr(values, job);
                Marshal.WriteIntPtr(values, IntPtr.Size, input);
                Marshal.WriteIntPtr(values, 2 * IntPtr.Size, output);
                Marshal.WriteIntPtr(values, 3 * IntPtr.Size, error);
                if (!UpdateProcThreadAttribute(list, 0, new IntPtr(0x2000D), values, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero) ||
                    !UpdateProcThreadAttribute(list, 0, new IntPtr(0x20002), IntPtr.Add(values, IntPtr.Size), new IntPtr(3 * IntPtr.Size), IntPtr.Zero, IntPtr.Zero)) throw Error();
                var startup = new StartupEx();
                startup.Startup.Size = Marshal.SizeOf(typeof(StartupEx)); startup.Attributes = list;
                startup.Startup.Flags = 0x100; // STARTF_USESTDHANDLES
                startup.Startup.Input = input; startup.Startup.Output = output; startup.Startup.Error = error;
                // JOB_LIST assigns membership before any code can run. Suspend until
                // our temporary job handle is closed, leaving lifetime with the guardian.
                if (!CreateProcess(exe, command, IntPtr.Zero, IntPtr.Zero, true, 0x08080004,
                    IntPtr.Zero, directory, ref startup, out process)) throw Error();
                CloseHandle(job); job = IntPtr.Zero;
                if (ResumeThread(process.Thread) == uint.MaxValue) throw Error();
                if (WaitForSingleObject(process.Process, uint.MaxValue) != 0) throw Error();
                exited = true; uint code;
                if (!GetExitCodeProcess(process.Process, out code)) throw Error();
                return unchecked((int)code);
            } finally {
                bool uncertain = false;
                if (process.Process != IntPtr.Zero && !exited) {
                    TerminateProcess(process.Process, 1);
                    uncertain = WaitForSingleObject(process.Process, 10000) != 0;
                }
                if (process.Thread != IntPtr.Zero) CloseHandle(process.Thread);
                if (process.Process != IntPtr.Zero) CloseHandle(process.Process);
                if (initialized) DeleteProcThreadAttributeList(list);
                if (list != IntPtr.Zero) Marshal.FreeHGlobal(list);
                if (values != IntPtr.Zero) Marshal.FreeHGlobal(values);
                if (input != IntPtr.Zero) CloseHandle(input);
                if (output != IntPtr.Zero) CloseHandle(output);
                if (error != IntPtr.Zero) CloseHandle(error);
                if (job != IntPtr.Zero) CloseHandle(job);
                if (uncertain) throw new InvalidOperationException("BASALTWATER_CLEANUP_UNCERTAIN: Native process did not confirm exit");
            }
        }
    }
    public sealed class DevelopmentStreamResult {
        public long RetainedBytes { get; set; }
        public long DiscardedBytes { get; set; }
        public int LimitBytes { get; set; }
        public bool Truncated { get { return DiscardedBytes > 0; } }
    }
    public static class DevelopmentStreams {
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern uint SetThreadExecutionState(uint flags);
        public static void Awake(bool active) {
            if (SetThreadExecutionState(active ? 0x80000001u : 0x80000000u) == 0)
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }
        public static async Task<DevelopmentStreamResult> Drain(Stream source, string path, int limit) {
            var result = new DevelopmentStreamResult { LimitBytes = limit };
            using (var output = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.ReadWrite)) {
                byte[] buffer = new byte[8192]; int total = 0; int count;
                while ((count = await source.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) > 0) {
                    int keep = Math.Min(count, limit - total);
                    if (keep > 0) {
                        await output.WriteAsync(buffer, 0, keep).ConfigureAwait(false);
                        await output.FlushAsync().ConfigureAwait(false); total += keep;
                    }
                    result.RetainedBytes += keep;
                    result.DiscardedBytes += count - keep;
                    // Always drain even after the cap, so a noisy child cannot deadlock.
                }
            }
            return result;
        }
    }
}
'@
}
