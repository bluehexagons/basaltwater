// Loaded only in the bounded Windows PowerShell worker. No API runs at load time.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Automation;

namespace Basaltwater.Desktop {
public sealed class Window {
    public long id; public int pid; public string identity; public string title;
    public int x, y, width, height; public bool active, minimized, maximized;
}
public sealed class Element {
    public string reference, name, role, runtime; public bool enabled, focused;
    public long window; public string identity; public int pid; public int[] path;
    public string[] actions;
}
public sealed class Scan { public List<Element> elements = new List<Element>(); public bool truncated; public int visited; }
public sealed class Monitor { public string id; public int x,y,width,height; }
public static class Native {
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int left, top, right, bottom; }
    [StructLayout(LayoutKind.Sequential)] struct Point { public int x,y; }
    delegate bool EnumProc(IntPtr h, IntPtr p);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr p);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr h);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out int p);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder b, int n);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder b, int n);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out Rect r);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindowAsync(IntPtr h, int n);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr a, int x,int y,int w,int z,uint flags);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
    [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint f,bool inherit,uint access);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint thread);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr h,int n,StringBuilder b,int length,out int needed);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr p,uint a,out IntPtr t);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out Point p);
    [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
    [StructLayout(LayoutKind.Sequential)] struct Input { public uint type; public InputUnion data; }
    [StructLayout(LayoutKind.Explicit)] struct InputUnion { [FieldOffset(0)] public Keyboard key; [FieldOffset(0)] public Mouse mouse; }
    [StructLayout(LayoutKind.Sequential)] struct Keyboard { public ushort vk,scan; public uint flags,time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Sequential)] struct Mouse { public int x,y; public uint data,flags,time; public UIntPtr extra; }
    [DllImport("user32.dll",SetLastError=true)] static extern uint SendInput(uint n,Input[] inputs,int size);

    static string DesktopName(IntPtr h) { var b=new StringBuilder(256); int n; if(h==IntPtr.Zero || !GetUserObjectInformation(h,2,b,512,out n)) throw new Exception("Interactive desktop unavailable"); return b.ToString(); }
    public static void Ready() {
        if(!Environment.UserInteractive || Process.GetCurrentProcess().SessionId==0) throw new Exception("Interactive account session required");
        var h=OpenInputDesktop(0,false,1);
        try { if(DesktopName(h)!="Default" || DesktopName(GetThreadDesktop(GetCurrentThreadId()))!="Default") throw new Exception("Locked, secure or alternate desktop: unsupported"); }
        finally { if(h!=IntPtr.Zero) CloseDesktop(h); }
    }
    public static void Dpi() { if(SetThreadDpiAwarenessContext(new IntPtr(-4))==IntPtr.Zero) throw new Exception("Per-monitor DPI awareness unavailable"); }
    static string Owner(Process p) { IntPtr t; if(!OpenProcessToken(p.Handle,8,out t)) throw new Exception("Cannot establish target owner"); try { using(var i=new WindowsIdentity(t)) return i.User.Value; } finally { CloseHandle(t); } }
    public static Window Observe(long id) {
        Ready(); var h=new IntPtr(id); int pid;
        if(!IsWindow(h) || !IsWindowVisible(h)) throw new Exception("Stale or hidden window handle"); GetWindowThreadProcessId(h,out pid);
        using(var p=Process.GetProcessById(pid)) {
            if(p.SessionId!=Process.GetCurrentProcess().SessionId || Owner(p)!=WindowsIdentity.GetCurrent().User.Value) throw new Exception("Target is outside current account/session");
            var b=new StringBuilder(513); GetWindowText(h,b,b.Capacity); var c=new StringBuilder(256); GetClassName(h,c,c.Capacity);
            Rect r; if(!GetWindowRect(h,out r)) throw new Exception("Window geometry unavailable");
            return new Window { id=id,pid=pid,identity=pid+":"+p.StartTime.ToUniversalTime().Ticks+":"+c+":"+b,
                title=b.ToString(),x=r.left,y=r.top,width=r.right-r.left,height=r.bottom-r.top,
                active=h==GetForegroundWindow(), minimized=IsIconic(h),maximized=IsZoomed(h) };
        }
    }
    public static List<Window> Windows() {
        Ready(); var result=new List<Window>();
        if(!EnumWindows(delegate(IntPtr h,IntPtr unused) { if(IsWindowVisible(h)) { try { result.Add(Observe(h.ToInt64())); } catch { /* inaccessible/other-account windows are not inventory */ } } return true; },IntPtr.Zero)) throw new Exception("Window enumeration failed");
        return result;
    }
    public static List<Monitor> Monitors() {
        Ready(); var result=new List<Monitor>();
        foreach(var screen in System.Windows.Forms.Screen.AllScreens) result.Add(new Monitor {id=screen.DeviceName,x=screen.Bounds.X,y=screen.Bounds.Y,width=screen.Bounds.Width,height=screen.Bounds.Height});
        return result;
    }
    public static Window Check(long id,string identity) { var w=Observe(id); if(w.identity!=identity) throw new Exception("Stale window identity; observe again"); return w; }
    public static Window Change(long id,string identity,string operation,int x,int y,int width,int height) {
        var w=Check(id,identity); var h=new IntPtr(id); bool requested=true;
        switch(operation) {
            case "focus": requested=SetForegroundWindow(h); break;
            case "move": requested=SetWindowPos(h,IntPtr.Zero,x,y,0,0,0x15); break;
            case "resize": requested=SetWindowPos(h,IntPtr.Zero,0,0,width,height,0x16); break;
            case "maximize": ShowWindowAsync(h,3); break;
            case "minimize": ShowWindowAsync(h,6); break;
            case "restore": ShowWindowAsync(h,9); break;
            case "close": requested=PostMessage(h,0x10,IntPtr.Zero,IntPtr.Zero); break;
            default: throw new Exception("Unsupported window operation");
        }
        if(!requested) throw new Exception("Window manager rejected request");
        for(int i=0;i<20;i++) {
            Thread.Sleep(50);
            if(operation=="close" && !IsWindow(h)) return null;
            w=Check(id,identity); // close may show a save dialog: do not claim success
            bool ok=(operation=="focus" && w.active)||(operation=="move" && w.x==x && w.y==y)||
                (operation=="resize" && w.width==width && w.height==height)||(operation=="maximize" && w.maximized)||
                (operation=="minimize" && w.minimized)||(operation=="restore" && !w.minimized && !w.maximized);
            if(ok) return w;
        }
        throw new Exception("Window outcome not observed; inspect before retrying");
    }
    static string Runtime(AutomationElement e) { return String.Join(".",e.GetRuntimeId()); }
    static AutomationElement Resolve(Element r) {
        Check(r.window,r.identity); var e=AutomationElement.FromHandle(new IntPtr(r.window));
        foreach(int index in r.path) { var child=TreeWalker.ControlViewWalker.GetFirstChild(e); for(int j=0;j<index && child!=null;j++) child=TreeWalker.ControlViewWalker.GetNextSibling(child); e=child; if(e==null) throw new Exception("Stale element ancestry"); }
        var c=e.Current;
        if(c.ProcessId!=r.pid || c.IsPassword || c.IsOffscreen || !c.IsEnabled || Runtime(e)!=r.runtime || c.Name!=r.name || c.ControlType.ProgrammaticName!=r.role) throw new Exception("Stale or unavailable element");
        return e;
    }
    public static Scan Inspect(int pid,Element root,string name,string role) {
        if(String.IsNullOrEmpty(name)) name=null; if(String.IsNullOrEmpty(role)) role=null;
        var scan=new Scan(); var clock=Stopwatch.StartNew();
        if(root!=null) { if(root.pid!=pid) throw new Exception("Root PID mismatch"); Walk(Resolve(root),root.window,root.identity,root.path,scan,clock,name,role); }
        else {
            bool found=false;
            foreach(var w in Windows()) if(w.pid==pid) { found=true; Walk(AutomationElement.FromHandle(new IntPtr(w.id)),w.id,w.identity,new int[0],scan,clock,name,role); }
            if(!found) throw new Exception("No accessible current-account/session window for PID");
        }
        return scan;
    }
    static void Walk(AutomationElement e,long window,string identity,int[] path,Scan scan,Stopwatch clock,string name,string role) {
        if(scan.visited>=512 || scan.elements.Count>=128 || path.Length>20 || clock.ElapsedMilliseconds>2500) { scan.truncated=true; return; }
        scan.visited++; var c=e.Current; if(c.IsPassword || c.IsOffscreen) return;
        if(c.ProcessId!=Int32.Parse(identity.Split(':')[0])) { scan.truncated=true; return; } // embedded foreign process is outside this PID scope
        if((name==null || c.Name==name) && (role==null || c.ControlType.ProgrammaticName==role)) {
            object pattern; var actions=new List<string>(); if(e.TryGetCurrentPattern(InvokePattern.Pattern,out pattern)) actions.Add("invoke");
            if(e.TryGetCurrentPattern(ValuePattern.Pattern,out pattern) && !((ValuePattern)pattern).Current.IsReadOnly) actions.Add("set-text");
            actions.Add("focus");
            scan.elements.Add(new Element {reference=Guid.NewGuid().ToString("N"),name=c.Name,role=c.ControlType.ProgrammaticName,
                runtime=Runtime(e),window=window,identity=identity,pid=c.ProcessId,path=path,enabled=c.IsEnabled,focused=c.HasKeyboardFocus,actions=actions.ToArray()});
        }
        var child=TreeWalker.ControlViewWalker.GetFirstChild(e); int index=0;
        while(child!=null) { if(scan.visited>=512 || scan.elements.Count>=128 || clock.ElapsedMilliseconds>2500) { scan.truncated=true; break; }
            var next=new int[path.Length+1]; path.CopyTo(next,0); next[path.Length]=index++;
            Walk(child,window,identity,next,scan,clock,name,role); child=TreeWalker.ControlViewWalker.GetNextSibling(child);
        }
    }
    public static bool Act(Element r,string operation,string text) {
        var e=Resolve(r); object p;
        // Re-resolve immediately before mutation; UIA providers can still race.
        switch(operation) {
            case "focus": e.SetFocus(); return e.Current.HasKeyboardFocus;
            case "set-text": if(text==null || text.Length>4096) throw new Exception("Text limit is 4096");
                if(!e.TryGetCurrentPattern(ValuePattern.Pattern,out p) || ((ValuePattern)p).Current.IsReadOnly) throw new Exception("Editable ValuePattern unavailable");
                ((ValuePattern)p).SetValue(text); return ((ValuePattern)p).Current.Value==text;
            case "invoke": if(!e.TryGetCurrentPattern(InvokePattern.Pattern,out p)) throw new Exception("InvokePattern unavailable");
                ((InvokePattern)p).Invoke(); return false; // application acceptance cannot prove task completion
            default: throw new Exception("Unsupported element operation");
        }
    }
    public static void Capture(long id,string identity,string monitor,string output) {
        if(String.IsNullOrEmpty(monitor)) monitor=null;
        Ready(); Rectangle rect;
        if(monitor!=null) { var matches=new List<System.Windows.Forms.Screen>(); foreach(var s in System.Windows.Forms.Screen.AllScreens) if(s.DeviceName==monitor) matches.Add(s);
            if(matches.Count!=1) throw new Exception("Unknown explicit monitor device name"); rect=matches[0].Bounds;
        } else {
            var w=Check(id,identity); if(w.minimized) throw new Exception("Cannot capture minimized window");
            rect=new Rectangle(w.x,w.y,w.width,w.height);
        }
        if(rect.Width<=0 || rect.Height<=0 || rect.Width>16384 || rect.Height>16384 || (long)rect.Width*rect.Height>64000000) throw new Exception("Capture geometry exceeds pixel budget");
        using(var bitmap=new Bitmap(rect.Width,rect.Height)) { using(var g=Graphics.FromImage(bitmap)) {
                if(monitor!=null) g.CopyFromScreen(rect.Left,rect.Top,0,0,rect.Size);
                else { var dc=g.GetHdc(); try { Check(id,identity); if(!PrintWindow(new IntPtr(id),dc,2)) throw new Exception("Application does not support window capture"); } finally { g.ReleaseHdc(dc); } }
            }
            using(var file=new FileStream(output,FileMode.CreateNew,FileAccess.Write,FileShare.None)) bitmap.Save(file,ImageFormat.Png);
        }
    }
    static void Send(Input[] items) { if(SendInput((uint)items.Length,items,Marshal.SizeOf(typeof(Input)))!=items.Length) throw new Exception("Input rejected or partial; observe before retrying"); }
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    static Input Key(ushort vk,ushort scan,uint flags) { return new Input {type=1,data=new InputUnion {key=new Keyboard {vk=vk,scan=scan,flags=flags}}}; }
    public static void InputAction(long id,string identity,string kind,string text,int x,int y,int width,int height) {
        var w=Check(id,identity); if(!w.active || w.minimized || w.width!=width || w.height!=height) throw new Exception("Target must remain foreground with observed geometry");
        foreach(int key in new [] {16,17,18,91,92,1,2,4}) if((GetAsyncKeyState(key)&0x8000)!=0) throw new Exception("Physical modifier or mouse button held; input refused");
        if(kind=="click") {
            if(x<0 || y<0 || x>=w.width || y>=w.height) throw new Exception("Click outside window");
            // Reject occlusion and borders using WindowFromPoint.
            var point=new Point {x=w.x+x,y=w.y+y}; var target=WindowFromPoint(point); int owner; GetWindowThreadProcessId(target,out owner);
            if(owner!=w.pid || GetAncestor(target,2)!=new IntPtr(id)) throw new Exception("Click target occluded or changed");
            if(!SetCursorPos(point.x,point.y)) throw new Exception("Pointer move rejected"); Point observed;
            if(!GetCursorPos(out observed) || observed.x!=point.x || observed.y!=point.y) throw new Exception("Pointer destination unverified");
            Check(id,identity); if(GetForegroundWindow()!=new IntPtr(id)) throw new Exception("Foreground changed");
            Send(new [] {new Input {data=new InputUnion {mouse=new Mouse {flags=2}}},new Input {data=new InputUnion {mouse=new Mouse {flags=4}}}});
        } else if(kind=="text") {
            if(text==null || text.Length>1024) throw new Exception("Input text limit is 1024");
            foreach(char c in text) { Check(id,identity); if(GetForegroundWindow()!=new IntPtr(id)) throw new Exception("Foreground changed"); Send(new [] {Key(0,c,4),Key(0,c,6)}); }
        } else if(kind=="key") {
            var keys=new Dictionary<string,ushort>(StringComparer.OrdinalIgnoreCase) {{"ENTER",13},{"ESC",27},{"TAB",9},{"SPACE",32},{"LEFT",37},{"UP",38},{"RIGHT",39},{"DOWN",40},{"BACKSPACE",8},{"DELETE",46}};
            ushort vk;
            if(text!=null && text.Length==1 && ((text[0]>='a' && text[0]<='z') || (text[0]>='A' && text[0]<='Z') || (text[0]>='0' && text[0]<='9'))) vk=(ushort)Char.ToUpperInvariant(text[0]);
            else if(!keys.TryGetValue(text??"",out vk)) throw new Exception("Unsupported key; use A-Z, 0-9, ENTER ESC TAB SPACE LEFT UP RIGHT DOWN BACKSPACE DELETE");
            Send(new [] {Key(vk,0,0),Key(vk,0,2)});
        } else throw new Exception("Unsupported input kind");
    }
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(Point p);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr h,uint flags);
}
}
