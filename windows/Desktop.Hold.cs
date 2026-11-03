// Compiled with Desktop.Native.cs. The Scheduled Task watchdog is outside the
// caller's process tree. Neither process owns a handle to a development Job.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace Basaltwater.Desktop {
public sealed class HeldKey { public ushort vk, scan; public uint flags; }
public sealed class HeldPlan {
    public long window, layout; public string identity; public int width, height;
    public HeldKey[] keys;
}
public sealed class HeldStamp {
    public int sequence, requested, accepted; public string action;
    public long scheduled_qpc, actual_qpc; public double lateness_ms;
}
public sealed class HeldReceipt {
    public string reason, release_state, utc_anchor, error;
    public long qpc_anchor, qpc_frequency, started_qpc, ended_qpc;
    public int transferred_mask, pending_mask;
    public bool outcome_verified = false;
    public List<HeldStamp> sends = new List<HeldStamp>();
}
// All shared ownership changes, including intent BEFORE SendInput, use this
// mutex. A dead driver leaves a conservative pending mask for the watchdog.
public sealed class HeldStore : IDisposable {
    readonly Mutex mutex; readonly string path;
    public sealed class State {
        public int magic = 0x484f4c44, phase, mask, pid, transferred;
        public long created, start, deadline, heartbeat;
    }
    public HeldStore(string directory,string name) {
        path=Path.Combine(directory,"ownership.bin");
        if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0) throw new Exception("Held-input ownership cannot use reparse paths");
        mutex=new Mutex(false,name);
    }
    public static void Create(string directory) {
        using(var f=new FileStream(Path.Combine(directory,"ownership.bin"),FileMode.CreateNew,FileAccess.Write,FileShare.ReadWrite)) { f.SetLength(64); }
    }
    public T Locked<T>(Func<State,T> action) {
        bool entered=false;
        try {
            try { entered=mutex.WaitOne(1000); } catch(AbandonedMutexException) { entered=true; }
            if(!entered) throw new Exception("Held-input ownership lock unavailable; cleanup uncertain");
            using(var f=new FileStream(path,FileMode.Open,FileAccess.ReadWrite,FileShare.ReadWrite)) {
                if(f.Length!=64) throw new Exception("Invalid held-input ownership record");
                var r=new BinaryReader(f); var s=new State(); int magic=r.ReadInt32();
                if(magic!=0 && magic!=s.magic) throw new Exception("Invalid held-input record version");
                s.phase=r.ReadInt32(); s.mask=r.ReadInt32(); s.pid=r.ReadInt32(); s.transferred=r.ReadInt32();
                s.created=r.ReadInt64(); s.start=r.ReadInt64(); s.deadline=r.ReadInt64(); s.heartbeat=r.ReadInt64();
                if(s.phase<0 || s.phase>3 || s.mask<0 || s.mask>7 || s.transferred<0 || s.transferred>7) throw new Exception("Invalid held-input ownership state");
                // Persist changes even when the action fails after a send intent.
                try { return action(s); }
                finally { f.Position=0; var w=new BinaryWriter(f); w.Write(s.magic); w.Write(s.phase); w.Write(s.mask); w.Write(s.pid); w.Write(s.transferred); w.Write(s.created); w.Write(s.start); w.Write(s.deadline); w.Write(s.heartbeat); w.Flush(); f.Flush(true); }
            }
        } finally { if(entered) mutex.ReleaseMutex(); }
    }
    // Intent must reach the file before entering the OS, not at callback return.
    public void Intent(State s) {
        using(var f=new FileStream(path,FileMode.Open,FileAccess.Write,FileShare.ReadWrite)) {
            var w=new BinaryWriter(f); w.Write(s.magic); w.Write(s.phase); w.Write(s.mask); w.Write(s.pid); w.Write(s.transferred); w.Write(s.created); w.Write(s.start); w.Write(s.deadline); w.Write(s.heartbeat); w.Flush(); f.Flush(true);
        }
    }
    public void Dispose() { mutex.Dispose(); }
}
public static partial class Native {
    public static bool SettleUnstartedHold(string directory,string name) {
        using(var store=new HeldStore(directory,name)) return store.Locked<bool>(delegate(HeldStore.State s) {
            if(s.phase!=0 || s.mask!=0) return false;
            // Closing admission under the ownership mutex prevents a delayed
            // driver from pressing after a startup failure was reconciled.
            s.phase=2; return true;
        });
    }
    public static bool HeldReleaseConfirmed(string directory,string name) {
        using(var store=new HeldStore(directory,name)) return store.Locked<bool>(delegate(HeldStore.State s) { return s.phase==2 && s.mask==0 && s.transferred==0; });
    }
    static bool ReconcileHeldRelease(HeldStore store,HeldKey[] keys,Func<ushort,bool> isUp) {
        return store.Locked<bool>(delegate(HeldStore.State s) {
            if(s.phase!=2 && s.phase!=3) return false;
            foreach(var key in keys) if(!isUp(key.vk)) return false;
            s.mask=0; s.transferred=0; s.phase=2; return true;
        });
    }
    public static bool ObserveHeldReleased(HeldPlan plan,string directory,string name) {
        Ready();
        using(var store=new HeldStore(directory,name)) return ReconcileHeldRelease(store,plan.keys,delegate(ushort vk) { return (GetAsyncKeyState(vk)&0x8000)==0; });
    }
    public static void RetireHeldAfterBoot(string directory,string name) {
        using(var store=new HeldStore(directory,name)) store.Locked<int>(delegate(HeldStore.State s) { s.mask=0; s.transferred=0; s.phase=2; return 0; });
    }
    public static HeldPlan PrepareHold(long id,string identity,string text,int width,int height) {
        var plan=new HeldPlan {window=id,identity=identity,width=width,height=height};
        HoldGuard(plan,true);
        int pid; var thread=GetWindowThreadProcessId(new IntPtr(id),out pid); var layout=GetKeyboardLayout(thread);
        var vks=new List<ushort>();
        var inputs=KeyInputs(text,false,delegate(ushort vk) { vks.Add(vk); return MapVirtualKeyEx(vk,4,layout); });
        if(vks.Count<1 || vks.Count>3) throw new Exception("Hold supports one key with optional distinct CTRL/SHIFT prefixes");
        plan.keys=new HeldKey[vks.Count]; plan.layout=layout.ToInt64();
        for(int i=0;i<vks.Count;i++) plan.keys[i]=new HeldKey {vk=vks[i],scan=inputs[i].data.key.scan,flags=inputs[i].data.key.flags};
        HoldGuard(plan,true); return plan;
    }
    public static void HoldGuard(HeldPlan plan,bool initial) {
        var w=Check(plan.window,plan.identity);
        if(!w.active || w.minimized || w.width!=plan.width || w.height!=plan.height) throw new Exception("Held target lost foreground, identity or geometry");
        int pid; var thread=GetWindowThreadProcessId(new IntPtr(plan.window),out pid);
        if(plan.layout!=0 && GetKeyboardLayout(thread).ToInt64()!=plan.layout) throw new Exception("Target keyboard layout changed");
        if(initial) {
            foreach(int key in new [] {16,17,18,91,92,1,2,4}) if((GetAsyncKeyState(key)&0x8000)!=0) throw new Exception("Physical modifier or mouse button held; input refused");
            if(plan.keys!=null) foreach(var key in plan.keys) if((GetAsyncKeyState(key.vk)&0x8000)!=0) throw new Exception("Requested key already held; input refused");
        }
    }
    static Input[] HeldInputs(HeldKey[] keys,int mask,bool up,UIntPtr marker) {
        var batch=new List<Input>();
        for(int n=0;n<keys.Length;n++) {
            int i=up ? keys.Length-1-n : n;
            if((mask&(1<<i))==0) continue;
            var k=keys[i]; var input=Key(0,k.scan,k.flags|(up ? 2u : 0u)); input.data.key.extra=marker; batch.Add(input);
        }
        return batch.ToArray();
    }
    static void Stamp(HeldReceipt receipt,string action,long scheduled,long actual,Input[] inputs,uint accepted) {
        receipt.sends.Add(new HeldStamp {sequence=receipt.sends.Count,action=action,scheduled_qpc=scheduled,actual_qpc=actual,
            lateness_ms=(actual-scheduled)*1000.0/Stopwatch.Frequency,requested=inputs.Length,accepted=(int)accepted});
    }
    static HeldReceipt NewHeldReceipt() { return new HeldReceipt {utc_anchor=DateTime.UtcNow.ToString("o"),qpc_anchor=Stopwatch.GetTimestamp(),qpc_frequency=Stopwatch.Frequency}; }
    static Process PinHeldProcess(int pid,long created) {
        var p=Process.GetProcessById(pid);
        try { var handle=p.Handle; if(p.StartTime.ToUniversalTime().Ticks!=created || p.SessionId!=Process.GetCurrentProcess().SessionId || Owner(p)!=System.Security.Principal.WindowsIdentity.GetCurrent().User.Value) throw new Exception("Held-input caller identity changed"); return p; }
        catch { p.Dispose(); throw; }
    }
    static void HeldRelease(HeldStore store,HeldPlan plan,EventWaitHandle[] transferred,UIntPtr marker,HeldReceipt receipt,string reason,Func<Input[],uint> send) {
        receipt.reason=reason;
        store.Locked<int>(delegate(HeldStore.State s) {
            receipt.started_qpc=s.start;
            if(s.phase==3) { receipt.release_state="uncertain"; receipt.pending_mask=s.mask; receipt.transferred_mask=s.transferred; return 0; }
            int mask=s.mask;
            for(int i=0;i<plan.keys.Length;i++) if((mask&(1<<i))!=0 && transferred[i].WaitOne(0)) { mask&=~(1<<i); s.transferred|=1<<i; }
            var batch=HeldInputs(plan.keys,mask,true,marker);
            if(batch.Length>0) {
                // Phase 3 before entry means a crash during release cannot be
                // mistaken for confirmed cleanup or trigger an automatic replay.
                s.phase=3; s.mask=mask; store.Intent(s);
                long actual=Stopwatch.GetTimestamp();
                long scheduled=(reason=="expired" || reason=="watchdog-expired") && s.deadline>0 ? s.deadline : actual;
                uint sent=send(batch); Stamp(receipt,"up",scheduled,actual,batch,sent);
                int remaining=mask, seen=0;
                for(int i=plan.keys.Length-1;i>=0;i--) if((mask&(1<<i))!=0 && seen++<sent) remaining&=~(1<<i);
                s.mask=remaining;
            } else s.mask=0;
            s.phase=s.mask==0 ? 2 : 3;
            receipt.transferred_mask=s.transferred; receipt.pending_mask=s.mask;
            receipt.release_state=s.phase==3 ? "uncertain" : s.transferred!=0 ? "physical-owner" : "submitted";
            return 0;
        });
        receipt.ended_qpc=Stopwatch.GetTimestamp();
    }
    static uint SendHeld(Input[] batch) { return SendInput((uint)batch.Length,batch,Marshal.SizeOf(typeof(Input))); }
    static void HeldPress(HeldStore store,HeldPlan plan,UIntPtr marker,HeldReceipt receipt,long start,long deadline,Func<Input[],uint> send) {
        store.Locked<int>(delegate(HeldStore.State s) {
            if(s.phase!=0 || start-s.heartbeat>Stopwatch.Frequency/4) throw new Exception("Watchdog stopped or heartbeat expired");
            s.phase=1; s.mask=(1<<plan.keys.Length)-1;
            using(var current=Process.GetCurrentProcess()) { s.pid=current.Id; s.created=current.StartTime.ToUniversalTime().Ticks; }
            s.start=start; s.deadline=deadline;
            store.Intent(s); var batch=HeldInputs(plan.keys,s.mask,false,marker);
            long actual=Stopwatch.GetTimestamp();
            if(actual>=deadline || actual-s.heartbeat>Stopwatch.Frequency/4) {
                s.mask=0; s.phase=2; throw new Exception("Lease or watchdog expired before down; no input submitted");
            }
            uint sent=send(batch); Stamp(receipt,"down",start,actual,batch,sent);
            if(sent>batch.Length) throw new Exception("Invalid input submission count");
            s.mask=(1<<(int)sent)-1;
            if(sent!=batch.Length) throw new Exception("Partial held input; no replay"); return 0;
        });
    }
    static string HeldCheck(HeldPlan plan,Func<string> control,Process caller,EventWaitHandle cancel,EventWaitHandle activity) {
        if(caller.HasExited) return "caller-exited";
        if(cancel.WaitOne(0)) return "cancelled";
        if(activity.WaitOne(0)) return "outside-input";
        var reason=control(); if(!String.IsNullOrEmpty(reason)) return reason;
        try { HoldGuard(plan,false); } catch(Exception e) { return e.Message; }
        return null;
    }
    static string HeldRepeatStop(long now,long scheduled,long deadline,long interval) {
        if(now>=deadline) return "expired";
        if(now-scheduled>Stopwatch.Frequency/10 || now-scheduled>=interval) return "schedule-late";
        return null;
    }
    public static HeldReceipt RunHold(HeldPlan plan,string directory,string name,ulong markerValue,int milliseconds,int repeatMilliseconds,int callerPid,long callerCreated,Func<string> control) {
        if(milliseconds<50 || milliseconds>5000 || (repeatMilliseconds!=0 && (repeatMilliseconds<50 || repeatMilliseconds>milliseconds))) throw new Exception("Hold bounds: 50-5000 ms; repeat 0 or 50..duration");
        var receipt=NewHeldReceipt(); var marker=new UIntPtr(markerValue);
        using(var store=new HeldStore(directory,name))
        using(var caller=PinHeldProcess(callerPid,callerCreated))
        using(var ready=EventWaitHandle.OpenExisting(name+".ready"))
        using(var cancel=EventWaitHandle.OpenExisting(name+".cancel"))
        using(var activity=EventWaitHandle.OpenExisting(name+".activity")) {
            var transferred=new EventWaitHandle[plan.keys.Length];
            try {
                for(int i=0;i<transferred.Length;i++) transferred[i]=EventWaitHandle.OpenExisting(name+".physical."+i);
                if(!ready.WaitOne(0)) throw new Exception("Held-input watchdog not ready");
                var reason=HeldCheck(plan,control,caller,cancel,activity); if(reason!=null) throw new Exception(reason);
                HoldGuard(plan,true);
                long start=Stopwatch.GetTimestamp(), deadline=start+milliseconds*Stopwatch.Frequency/1000;
                HeldPress(store,plan,marker,receipt,start,deadline,SendHeld);
                long next=repeatMilliseconds==0 ? deadline : start+repeatMilliseconds*Stopwatch.Frequency/1000;
                while(true) {
                    long now=Stopwatch.GetTimestamp(); if(now>=deadline) { reason="expired"; break; }
                    reason=HeldCheck(plan,control,caller,cancel,activity); if(reason!=null) break;
                    now=Stopwatch.GetTimestamp(); if(now>=deadline) { reason="expired"; break; }
                    bool alive=store.Locked<bool>(delegate(HeldStore.State s) { return s.phase==1 && now-s.heartbeat<=Stopwatch.Frequency/4; });
                    if(!alive) { reason="watchdog-stopped"; break; }
                    if(repeatMilliseconds!=0 && now>=next) {
                        bool repeated=store.Locked<bool>(delegate(HeldStore.State s) {
                            long actual=Stopwatch.GetTimestamp();
                            reason=HeldRepeatStop(actual,next,deadline,repeatMilliseconds*Stopwatch.Frequency/1000);
                            if(reason!=null) return false;
                            if(s.phase!=1 || actual-s.heartbeat>Stopwatch.Frequency/4) { reason="watchdog-stopped"; return false; }
                            var batch=HeldInputs(plan.keys,1<<(plan.keys.Length-1),false,marker);
                            actual=Stopwatch.GetTimestamp(); uint sent=SendHeld(batch); Stamp(receipt,"repeat",next,actual,batch,sent);
                            if(sent!=1) throw new Exception("Repeat rejected; no replay"); return true;
                        });
                        if(!repeated) break;
                        next+=repeatMilliseconds*Stopwatch.Frequency/1000;
                    }
                    Thread.Sleep(5);
                }
                HeldRelease(store,plan,transferred,marker,receipt,reason,SendHeld);
            } catch(Exception e) { receipt.error=e.Message; HeldRelease(store,plan,transferred,marker,receipt,"failed",SendHeld); }
            finally { foreach(var handle in transferred) if(handle!=null) handle.Dispose(); }
        }
        return receipt;
    }
    delegate IntPtr HeldHook(int code,IntPtr message,IntPtr data);
    [StructLayout(LayoutKind.Sequential)] struct HeldKeyboard { public uint vk,scan,flags,time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Sequential)] struct HeldMouse { public Point point; public uint data,flags,time; public UIntPtr extra; }
    [DllImport("user32.dll",SetLastError=true)] static extern IntPtr SetWindowsHookEx(int type,HeldHook hook,IntPtr module,uint thread);
    [DllImport("user32.dll")] static extern bool UnhookWindowsHookEx(IntPtr hook);
    [DllImport("user32.dll")] static extern IntPtr CallNextHookEx(IntPtr hook,int code,IntPtr message,IntPtr data);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern IntPtr GetModuleHandle(string name);
    [DllImport("user32.dll")] static extern bool PostThreadMessage(uint thread,uint message,IntPtr w,IntPtr l);
    static bool IsHeldPhysicalKey(HeldKey key,uint vk,uint scan,uint flags) {
        // Hooks can expose a generic modifier VK. Physical scan identity still
        // identifies our left modifier; E0 distinguishes keypad/navigation keys.
        return key.vk==vk || (key.scan==(scan&255) && ((key.flags^flags)&1)==0);
    }
    sealed class HeldHooks : IDisposable {
        readonly Thread thread; readonly ManualResetEvent ready=new ManualResetEvent(false);
        uint threadId; Exception failure;
        public bool Alive { get { return thread.IsAlive && failure==null; } }
        public HeldHooks(HeldPlan plan,UIntPtr marker,EventWaitHandle activity,EventWaitHandle[] transferred) {
            thread=new Thread(delegate() {
                IntPtr keyboard=IntPtr.Zero,mouse=IntPtr.Zero;
                HeldHook keyHook=delegate(int code,IntPtr message,IntPtr data) {
                    if(code>=0) { var k=(HeldKeyboard)Marshal.PtrToStructure(data,typeof(HeldKeyboard));
                        if(k.extra!=marker) { activity.Set(); for(int i=0;i<plan.keys.Length;i++) if(IsHeldPhysicalKey(plan.keys[i],k.vk,k.scan,k.flags)) transferred[i].Set(); }
                    } return CallNextHookEx(IntPtr.Zero,code,message,data);
                };
                HeldHook mouseHook=delegate(int code,IntPtr message,IntPtr data) {
                    if(code>=0) { var m=(HeldMouse)Marshal.PtrToStructure(data,typeof(HeldMouse)); if(m.extra!=marker) activity.Set(); }
                    return CallNextHookEx(IntPtr.Zero,code,message,data);
                };
                try {
                    threadId=GetCurrentThreadId();
                    keyboard=SetWindowsHookEx(13,keyHook,GetModuleHandle(null),0); mouse=SetWindowsHookEx(14,mouseHook,GetModuleHandle(null),0);
                    if(keyboard==IntPtr.Zero || mouse==IntPtr.Zero) throw new Exception("Physical-input ownership hooks unavailable");
                    // This thread does ONLY hook callbacks and message dispatch.
                    // File I/O, PS callbacks and ownership waits cannot starve it
                    // or deadlock SendInput against the shared ownership mutex.
                    using(var context=new ApplicationContext()) { Application.DoEvents(); ready.Set(); Application.Run(context); }
                } catch(Exception e) { failure=e; ready.Set(); }
                finally { if(keyboard!=IntPtr.Zero) UnhookWindowsHookEx(keyboard); if(mouse!=IntPtr.Zero) UnhookWindowsHookEx(mouse); GC.KeepAlive(keyHook); GC.KeepAlive(mouseHook); }
            });
            thread.IsBackground=true; thread.SetApartmentState(ApartmentState.STA); thread.Start();
            if(!ready.WaitOne(3000) || !Alive) { Dispose(); throw new Exception("Held-input ownership monitor failed",failure); }
        }
        public void Dispose() {
            if(thread.IsAlive) { PostThreadMessage(threadId,0x12,IntPtr.Zero,IntPtr.Zero); if(!thread.Join(3000)) throw new Exception("Held-input ownership monitor shutdown uncertain"); }
            ready.Dispose();
        }
    }
    public static HeldReceipt WatchHold(HeldPlan plan,string directory,string name,ulong markerValue,int callerPid,long callerCreated,Func<string> control) {
        var receipt=NewHeldReceipt(); var marker=new UIntPtr(markerValue); var transferred=new EventWaitHandle[plan.keys.Length];
        using(var store=new HeldStore(directory,name))
        using(var caller=PinHeldProcess(callerPid,callerCreated))
        using(var ready=new EventWaitHandle(false,EventResetMode.ManualReset,name+".ready"))
        using(var cancel=new EventWaitHandle(false,EventResetMode.ManualReset,name+".cancel"))
        using(var activity=new EventWaitHandle(false,EventResetMode.ManualReset,name+".activity")) {
            Process driver=null; HeldHooks hooks=null;
            try {
                for(int i=0;i<transferred.Length;i++) transferred[i]=new EventWaitHandle(false,EventResetMode.ManualReset,name+".physical."+i);
                hooks=new HeldHooks(plan,marker,activity,transferred);
                long startup=Stopwatch.GetTimestamp()+30*Stopwatch.Frequency; string reason=null;
                while(true) {
                    if(!hooks.Alive) { reason="ownership-monitor-stopped"; break; }
                    reason=HeldCheck(plan,control,caller,cancel,activity); if(reason!=null) break;
                    bool done=store.Locked<bool>(delegate(HeldStore.State s) {
                        s.heartbeat=Stopwatch.GetTimestamp();
                        if(s.phase==2 || s.phase==3) { reason="driver-settled"; return true; }
                        if(s.phase==1) {
                            if(driver==null) driver=PinHeldProcess(s.pid,s.created);
                            if(driver.HasExited) { reason="driver-exited"; return true; }
                            if(s.heartbeat>=s.deadline) { reason="watchdog-expired"; return true; }
                        } else if(s.heartbeat>=startup) { reason="startup-expired"; return true; }
                        return false;
                    });
                    if(done) break;
                    ready.Set(); Thread.Sleep(10);
                }
                HeldRelease(store,plan,transferred,marker,receipt,reason,SendHeld);
            } catch(Exception e) { receipt.error=e.Message; HeldRelease(store,plan,transferred,marker,receipt,"watchdog-failed",SendHeld); }
            finally {
                ready.Reset(); if(hooks!=null) hooks.Dispose(); if(driver!=null) driver.Dispose();
                foreach(var handle in transferred) if(handle!=null) handle.Dispose();
            }
        }
        return receipt;
    }
}
}
