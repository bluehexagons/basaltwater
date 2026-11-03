// Read-only token identity; usable in Windows PowerShell 5.1 and PowerShell 7.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;

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
    [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

    public static Process PinProcess(int pid,long created,string sid,int session,string authentication) {
        var process=Process.GetProcessById(pid); IntPtr token=IntPtr.Zero;
        try {
            var handle=process.Handle;
            if(process.HasExited || process.StartTime.ToUniversalTime().Ticks!=created || process.SessionId!=session ||
                !OpenProcessToken(handle,8,out token)) throw new Exception("Desktop worker process identity unavailable");
            using(var owner=new WindowsIdentity(token)) if(owner.User.Value!=sid || AuthenticationId(token)!=authentication)
                throw new Exception("Desktop worker process belongs to another account/logon");
            return process;
        } catch {process.Dispose();throw;}
        finally {if(token!=IntPtr.Zero) CloseHandle(token);}
    }

    public static string AuthenticationId(IntPtr token) {
        Statistics value; uint required;
        if (!GetTokenInformation(token, 10, out value, (uint)Marshal.SizeOf(typeof(Statistics)), out required))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return unchecked((uint)value.AuthenticationId.High).ToString("x8") + value.AuthenticationId.Low.ToString("x8");
    }
}
}
