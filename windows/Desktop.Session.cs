// Read-only token identity; usable in Windows PowerShell 5.1 and PowerShell 7.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace Basaltwater.Desktop {
public static class Session {
    [StructLayout(LayoutKind.Sequential)] struct Luid { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)] struct Statistics {
        public Luid TokenId, AuthenticationId;
        public long Expiration;
        public uint TokenType, Impersonation, Charged, Available, Groups, Privileges;
        public Luid ModifiedId;
    }
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool GetTokenInformation(IntPtr token, int kind, out Statistics value, uint length, out uint required);

    public static string AuthenticationId(IntPtr token) {
        Statistics value; uint required;
        if (!GetTokenInformation(token, 10, out value, (uint)Marshal.SizeOf(typeof(Statistics)), out required))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return unchecked((uint)value.AuthenticationId.High).ToString("x8") + value.AuthenticationId.Low.ToString("x8");
    }
}
}
