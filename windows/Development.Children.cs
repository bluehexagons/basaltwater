// Opt-in sampled identities. Only process handles survive a sample, never Job handles.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;

namespace Basaltwater.Diagnostics {
public sealed class Member {
    public int ProcessId;
    public string CreatedFileTimeUtc, Token, ParentToken, Role, State = "observed";
    public uint? ExitCode;
    public string ExitCodeHex;
    internal IntPtr Handle;
    internal long Created;
    internal int Parent;
}
public sealed class Children : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public IntPtr Reserved, Peb, Reserved2, Reserved3, Id, Parent;
    }
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr OpenJobObject(uint access,bool inherit,string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job,int kind,IntPtr data,int size,out int returned);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool IsProcessInJob(IntPtr process,IntPtr job,out bool member);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetProcessTimes(IntPtr process,out long created,out long exited,out long kernel,out long user);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr token,int kind,out int value,int size,out int returned);
    [DllImport("ntdll.dll")] static extern int NtQueryInformationProcess(IntPtr process,int kind,out Basic basic,int size,out int returned);
    readonly List<Member> members = new List<Member>();
    readonly string sid;
    readonly int session, helper;
    public bool Overflow { get; private set; }
    public string Error { get; private set; }
    public Member[] Members { get { return members.ToArray(); } }
    public Children(string owner,int sessionId,int helperPid) {
        sid=owner; session=sessionId; helper=helperPid;
        using(var caller=WindowsIdentity.GetCurrent()) using(var self=Process.GetCurrentProcess())
            if(caller.User.Value!=sid || self.SessionId!=session) throw new Exception("Child tracking account/session mismatch");
    }
    bool ObserveExits() {
        bool changed=false;
        foreach(var m in members) if(m.Handle!=IntPtr.Zero && WaitForSingleObject(m.Handle,0)==0) {
            uint code;
            if(GetExitCodeProcess(m.Handle,out code)) {
                m.ExitCode=code; m.ExitCodeHex="0x"+code.ToString("X8"); m.State="exited";
                CloseHandle(m.Handle); m.Handle=IntPtr.Zero; changed=true;
            }
        }
        return changed;
    }
    public bool Poll(string jobName,int rootPid,long rootCreated,bool enumerate) {
        bool changed=ObserveExits();
        var pending=new List<Member>();
        string previousError=Error; bool previousOverflow=Overflow;
        if(enumerate) {
            IntPtr job=IntPtr.Zero, data=IntPtr.Zero;
            try {
                job=OpenJobObject(4,false,jobName);
                if(job==IntPtr.Zero) throw new Exception("Child Job query unavailable");
                const int size=8+4096*8;
                data=Marshal.AllocHGlobal(size); int returned;
                if(!QueryInformationJobObject(job,3,data,size,out returned)) throw new Exception("Child Job enumeration unavailable or exceeds 4096 members");
                int count=Marshal.ReadInt32(data,4);
                if(count<0 || count>4096) throw new Exception("Invalid Job member count");
                if(Marshal.ReadInt32(data,0)>count) Overflow=true;
                for(int i=0;i<count;i++) {
                    long id=IntPtr.Size==8 ? Marshal.ReadInt64(data,8+i*8) : Marshal.ReadInt32(data,8+i*4);
                    if(id<=0 || id>int.MaxValue) continue;
                    // Existing live handles pin identity; a recycled PID can only
                    // appear after ObserveExits closes the old, signalled handle.
                    if(members.Exists(m=>m.ProcessId==(int)id && m.Handle!=IntPtr.Zero)) continue;
                    if(members.Count+pending.Count>=128) { Overflow=true; continue; }
                    IntPtr handle=OpenProcess(0x101000,false,(int)id);
                    if(handle==IntPtr.Zero) { Error="Some sampled members could not be opened"; continue; }
                    bool keep=false;
                    try {
                        bool member; long created,exited,kernel,user;
                        if(!IsProcessInJob(handle,job,out member) || !member ||
                            !GetProcessTimes(handle,out created,out exited,out kernel,out user)) { Error="Some sampled identities could not be verified"; continue; }
                        string token=id+":"+created;
                        if(members.Exists(m=>m.Token==token)) continue;
                        pending.Add(new Member { ProcessId=(int)id,Created=created,CreatedFileTimeUtc=created.ToString(),Token=token,Handle=handle }); keep=true;
                    } finally { if(!keep) CloseHandle(handle); }
                }
            } catch(Exception failure) { Error=failure.Message; }
            finally { if(data!=IntPtr.Zero) Marshal.FreeHGlobal(data); if(job!=IntPtr.Zero) CloseHandle(job); }
        }
        // All account/session queries happen after the transient Job is closed.
        foreach(var m in pending) {
            IntPtr token=IntPtr.Zero; bool keep=false;
            try {
                if(!OpenProcessToken(m.Handle,8,out token)) throw new Exception("Some member owners unavailable");
                int value,returned;
                using(var owner=new WindowsIdentity(token)) if(owner.User.Value!=sid) throw new Exception("Member owner mismatch");
                if(!GetTokenInformation(token,12,out value,4,out returned) || value!=session) throw new Exception("Member session mismatch");
                Basic basic;
                if(NtQueryInformationProcess(m.Handle,0,out basic,Marshal.SizeOf(typeof(Basic)),out returned)==0) m.Parent=basic.Parent.ToInt32();
                members.Add(m); keep=true; changed=true;
            } catch(Exception failure) { Error=failure.Message; }
            finally { if(token!=IntPtr.Zero) CloseHandle(token); if(!keep) CloseHandle(m.Handle); }
        }
        foreach(var m in members) {
            string role=m.ProcessId==helper ? "helper" : m.ProcessId==rootPid && m.Created==rootCreated ? "root" : "child";
            if(m.Role!=role) { m.Role=role; changed=true; }
            // Link only a pinned, live parent created before this child. Never
            // attach an ancestor discovered after its PID might have been reused.
            if(m.ParentToken==null) foreach(var parent in members)
                if(parent.ProcessId==m.Parent && parent.Created<=m.Created && parent.Handle!=IntPtr.Zero && WaitForSingleObject(parent.Handle,0)==258) {
                    m.ParentToken=parent.Token; changed=true; break;
                }
        }
        return ObserveExits() || changed || previousError!=Error || previousOverflow!=Overflow;
    }
    public void Dispose() {
        foreach(var m in members) if(m.Handle!=IntPtr.Zero) { CloseHandle(m.Handle); m.Handle=IntPtr.Zero; }
    }
}
}
