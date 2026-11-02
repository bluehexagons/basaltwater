// Resource counters only: no debugger, memory contents, window messages or mutation.
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;

namespace Basaltwater.Diagnostics {
public sealed class Snapshot {
    public int ProcessId,ThreadCount; public uint HandleCount;
    public double KernelCpuSeconds,UserCpuSeconds;
    public ulong WorkingSetBytes,PrivateBytes;
    public string CapturedUtc;
}
public static class Native {
    [StructLayout(LayoutKind.Sequential)] struct Memory {
        public uint Size,PageFaults;
        public UIntPtr PeakWorkingSet,WorkingSet,PeakPagedPool,PagedPool,PeakNonPagedPool,NonPagedPool,Pagefile,PeakPagefile,Private;
    }
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr OpenJobObject(uint access,bool inherit,string name);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool IsProcessInJob(IntPtr process,IntPtr job,out bool member);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetProcessTimes(IntPtr process,out long created,out long exited,out long kernel,out long user);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool K32GetProcessMemoryInfo(IntPtr process,ref Memory memory,uint size);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetProcessHandleCount(IntPtr process,out uint count);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
    static Exception Error() { return new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error()); }
    public static Snapshot Capture(int pid,long creation,string jobName,string sid,int session) {
        if(pid<=0 || creation<=0 || String.IsNullOrEmpty(jobName)) throw new Exception("Native target identity unavailable");
        using(var caller=WindowsIdentity.GetCurrent()) using(var self=Process.GetCurrentProcess())
            if(caller.User.Value!=sid || self.SessionId!=session) throw new Exception("Diagnostics require the task's account and session");
        IntPtr process=OpenProcess(0x101000,false,pid),job=IntPtr.Zero,token=IntPtr.Zero;
        if(process==IntPtr.Zero) throw Error();
        try {
            long created,exited,kernel,user;
            if(!GetProcessTimes(process,out created,out exited,out kernel,out user)) throw Error();
            if(created!=creation) throw new Exception("Stale native target identity");
            job=OpenJobObject(4,false,jobName); if(job==IntPtr.Zero) throw Error();
            bool member; if(!IsProcessInJob(process,job,out member)) throw Error();
            if(!member) throw new Exception("Target is outside the task Job Object");
            // Never keep the guardian's kill-on-close Job alive during slower
            // token, memory or thread queries. Membership cannot be removed.
            CloseHandle(job); job=IntPtr.Zero;
            if(!OpenProcessToken(process,8,out token)) throw Error();
            using(var identity=new WindowsIdentity(token)) if(identity.User.Value!=sid) throw new Exception("Native target owner changed");
            var memory=new Memory {Size=(uint)Marshal.SizeOf(typeof(Memory))}; uint handles;
            if(!K32GetProcessMemoryInfo(process,ref memory,memory.Size) || !GetProcessHandleCount(process,out handles)) throw Error();
            int threads;
            using(var observed=Process.GetProcessById(pid)) {
                if(observed.SessionId!=session) throw new Exception("Native target session changed");
                threads=observed.Threads.Count;
            }
            if(WaitForSingleObject(process,0)!=258) throw new Exception("Native target exited during diagnostics");
            return new Snapshot {ProcessId=pid,ThreadCount=threads,HandleCount=handles,
                KernelCpuSeconds=kernel/10000000.0,UserCpuSeconds=user/10000000.0,
                WorkingSetBytes=memory.WorkingSet.ToUInt64(),PrivateBytes=memory.Private.ToUInt64(),CapturedUtc=DateTime.UtcNow.ToString("o")};
        } finally { if(token!=IntPtr.Zero) CloseHandle(token); if(job!=IntPtr.Zero) CloseHandle(job); CloseHandle(process); }
    }
}
}
