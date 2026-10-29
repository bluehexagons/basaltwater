<# Bounded stream draining and a thread-scoped idle-sleep request. No power-plan changes. #>
function Import-DevelopmentProcessType {
    if ('Basaltwater.DevelopmentStreams' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Threading.Tasks;
using System.Runtime.InteropServices;
namespace Basaltwater {
    public static class DevelopmentStreams {
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern uint SetThreadExecutionState(uint flags);
        public static void Awake(bool active) {
            if (SetThreadExecutionState(active ? 0x80000001u : 0x80000000u) == 0)
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }
        public static async Task Drain(Stream source, string path, int limit) {
            using (var output = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.ReadWrite)) {
                byte[] buffer = new byte[8192]; int total = 0; int count;
                while ((count = await source.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) > 0) {
                    int keep = Math.Min(count, limit - total);
                    if (keep > 0) {
                        await output.WriteAsync(buffer, 0, keep).ConfigureAwait(false);
                        await output.FlushAsync().ConfigureAwait(false); total += keep;
                    }
                    // Always drain even after the cap, so a noisy child cannot deadlock.
                }
            }
        }
    }
}
'@
}
