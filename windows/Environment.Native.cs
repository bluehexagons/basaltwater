// Read-only display and power configuration. Loaded by the bounded metadata worker.
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Runtime.InteropServices;

[assembly: DefaultDllImportSearchPaths(DllImportSearchPath.System32)]

namespace Basaltwater.EnvironmentInfo {
public sealed class Display {
    public string Id; public int X,Y,Width,Height; public bool Primary;
    public uint? RefreshRateHz; public int? ScalePercent;
}
public sealed class PowerStatus {
    public bool? AcOnline, BatteryPresent, BatterySaver;
    public int? BatteryPercent;
}
public static class Native {
    [StructLayout(LayoutKind.Sequential)] struct SystemPowerStatus {
        public byte AcLineStatus,BatteryFlag,BatteryLifePercent,SystemStatusFlag;
        public uint BatteryLifeTime,BatteryFullLifeTime;
    }
    [DllImport("powrprof.dll")] static extern uint PowerGetActiveScheme(IntPtr root,out IntPtr scheme);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
    [DllImport("kernel32.dll",SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)] static extern bool GetSystemPowerStatus(out SystemPowerStatus status);
    public static string ActivePowerScheme() {
        IntPtr scheme=IntPtr.Zero;
        try {
            uint status=PowerGetActiveScheme(IntPtr.Zero,out scheme);
            if(status!=0) throw new Win32Exception((int)status,"Active power scheme unavailable");
            if(scheme==IntPtr.Zero) throw new Exception("Active power scheme returned no GUID");
            return ((Guid)Marshal.PtrToStructure(scheme,typeof(Guid))).ToString("D");
        } finally {
            if(scheme!=IntPtr.Zero) LocalFree(scheme);
        }
    }
    static PowerStatus ConvertPowerStatus(byte ac,byte battery,byte percent,byte saver) {
        var result=new PowerStatus();
        if(ac<=1) result.AcOnline=ac==1;
        if(battery!=255) result.BatteryPresent=(battery&128)==0;
        if(percent<=100 && result.BatteryPresent!=false) result.BatteryPercent=percent;
        if(saver<=1) result.BatterySaver=saver==1;
        return result;
    }
    public static PowerStatus SystemPower() {
        SystemPowerStatus status;
        if(!GetSystemPowerStatus(out status))
            throw new Win32Exception(Marshal.GetLastWin32Error(),"System power status unavailable");
        return ConvertPowerStatus(status.AcLineStatus,status.BatteryFlag,status.BatteryLifePercent,status.SystemStatusFlag);
    }
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left,Top,Right,Bottom; }
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct MonitorInfo {
        public uint Size; public Rect Bounds,Work; public uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string Device;
    }
    // DEVMODEW is 220 bytes on both x86 and x64. Only these documented fields
    // are used; no driver-private data is requested or retained.
    [StructLayout(LayoutKind.Explicit,Size=220)] struct DisplayMode {
        [FieldOffset(68)] public ushort Size;
        [FieldOffset(72)] public uint Fields;
        [FieldOffset(184)] public uint Frequency;
    }
    delegate bool MonitorProc(IntPtr monitor,IntPtr dc,ref Rect bounds,IntPtr data);
    [DllImport("user32.dll",SetLastError=true)] static extern bool EnumDisplayMonitors(IntPtr dc,IntPtr clip,MonitorProc callback,IntPtr data);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetMonitorInfo(IntPtr monitor,ref MonitorInfo info);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool EnumDisplaySettings(string device,uint mode,ref DisplayMode settings);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("shcore.dll")] static extern int GetScaleFactorForMonitor(IntPtr monitor,out int scale);
    static void ApplyMode(Display display,bool success,uint fields,uint frequency,int scaleResult,int scale) {
        // 0 and 1 mean the driver's default, not a measured refresh frequency.
        if(success && (fields&0x400000)!=0 && frequency>1) display.RefreshRateHz=frequency;
        // The API supplies a fallback value even on failure; never report it.
        if(scaleResult==0 && scale>0 && scale<=1000) display.ScalePercent=scale;
    }
    public static List<Display> Displays() {
        if(!System.Environment.UserInteractive || System.Diagnostics.Process.GetCurrentProcess().SessionId==0)
            throw new Exception("Display metadata requires an interactive account session");
        if(SetThreadDpiAwarenessContext(new IntPtr(-4))==IntPtr.Zero) throw new Exception("Physical display geometry unavailable: DPI context rejected");
        var result=new List<Display>(); Exception failure=null;
        bool enumerated=EnumDisplayMonitors(IntPtr.Zero,IntPtr.Zero,delegate(IntPtr monitor,IntPtr dc,ref Rect bounds,IntPtr data) {
            try {
                if(result.Count>=32) throw new Exception("Display inventory exceeds 32-monitor limit");
                var info=new MonitorInfo {Size=(uint)Marshal.SizeOf(typeof(MonitorInfo))};
                if(!GetMonitorInfo(monitor,ref info)) throw new Exception("Monitor geometry unavailable");
                var display=new Display {Id=info.Device,X=info.Bounds.Left,Y=info.Bounds.Top,
                    Width=info.Bounds.Right-info.Bounds.Left,Height=info.Bounds.Bottom-info.Bounds.Top,Primary=(info.Flags&1)!=0};
                var mode=new DisplayMode {Size=(ushort)Marshal.SizeOf(typeof(DisplayMode))};
                bool available=EnumDisplaySettings(info.Device,unchecked((uint)-1),ref mode);
                int scale; int status=GetScaleFactorForMonitor(monitor,out scale);
                ApplyMode(display,available,mode.Fields,mode.Frequency,status,scale);
                result.Add(display); return true;
            } catch(Exception error) { failure=error; return false; }
        },IntPtr.Zero);
        if(failure!=null) throw failure;
        if(!enumerated) throw new Exception("Display enumeration failed");
        return result;
    }
}
}
