using System;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace Basaltwater.WindowsRestart {
    // Notification only: no shutdown or desktop input is performed here.
    public static class Dialog {
        [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
        [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
        [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint thread);
        [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
        [DllImport("wtsapi32.dll")] static extern bool WTSQuerySessionInformation(IntPtr server,
            int session, int information, out IntPtr buffer, out int bytes);
        [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder value, int length, out int needed);

        static bool IsDefault(IntPtr handle) {
            var name = new StringBuilder(256);
            int needed;
            return handle != IntPtr.Zero && GetUserObjectInformation(handle, 2, name, 512, out needed) &&
                String.Equals(name.ToString(), "Default", StringComparison.OrdinalIgnoreCase);
        }

        public static bool CanNotify() {
            int session;
            using (var process = Process.GetCurrentProcess()) { session = process.SessionId; }
            if (!Environment.UserInteractive || session == 0) return false;
            IntPtr state;
            int bytes;
            // WTSConnectState (8): only WTSActive (0) can receive this warning.
            if (!WTSQuerySessionInformation(IntPtr.Zero, session, 8, out state, out bytes)) return false;
            try { if (state == IntPtr.Zero || bytes < 4 || Marshal.ReadInt32(state) != 0) return false; }
            finally { WTSFreeMemory(state); }
            var desktop = OpenInputDesktop(0, false, 1);
            try { return IsDefault(desktop) && IsDefault(GetThreadDesktop(GetCurrentThreadId())); }
            finally { if (desktop != IntPtr.Zero) CloseDesktop(desktop); }
        }

        public static bool Show(string reason, DateTime deadlineUtc) {
            // Never count down unseen on a locked, disconnected or secure desktop.
            if (!CanNotify() || (deadlineUtc - DateTime.UtcNow).TotalSeconds < 55) return false;
            using (var form = new Form())
            using (var timer = new Timer()) {
                form.Text = "Basaltwater: Windows restart required";
                form.Width = 600; form.Height = 290;
                form.StartPosition = FormStartPosition.CenterScreen;
                form.FormBorderStyle = FormBorderStyle.FixedDialog;
                form.MaximizeBox = false; form.MinimizeBox = false; form.TopMost = true;
                form.AutoScaleDimensions = new SizeF(96, 96);
                form.AutoScaleMode = AutoScaleMode.Dpi;
                var reasonLabel = new Label { Left = 20, Top = 20, Width = 540, Height = 65,
                    Text = "Windows needs to restart because: " + reason };
                var countdown = new Label { Left = 20, Top = 90, Width = 540, Height = 55 };
                var advice = new Label { Left = 20, Top = 145, Width = 540, Height = 40,
                    Text = "Save your work. Apps will not be forced closed. Later defers this restart." };
                var now = new Button { Text = "Restart now", Left = 310, Top = 195, Width = 120,
                    DialogResult = DialogResult.OK };
                var later = new Button { Text = "Later", Left = 440, Top = 195, Width = 120,
                    DialogResult = DialogResult.Cancel };
                form.Controls.AddRange(new Control[] { reasonLabel, countdown, advice, now, later });
                form.AcceptButton = later; form.CancelButton = later;
                var elapsed = Stopwatch.StartNew();
                double initialSeconds = (deadlineUtc - DateTime.UtcNow).TotalSeconds;
                double previousTick = 0;
                Action update = () => {
                    double current = elapsed.Elapsed.TotalSeconds;
                    // A suspended process/PC must not wake up to an immediate reboot.
                    double remaining = initialSeconds - current;
                    if (!CanNotify() || current - previousTick > 5 ||
                        Math.Abs((deadlineUtc - DateTime.UtcNow).TotalSeconds - remaining) > 5) {
                        form.DialogResult = DialogResult.Cancel; form.Close(); return;
                    }
                    previousTick = current;
                    int seconds = Math.Max(0, (int)Math.Ceiling(remaining));
                    countdown.Text = "Windows restarts in " + seconds + " seconds, at " +
                        deadlineUtc.ToLocalTime().ToString("T") + ".";
                    if (seconds == 0) { form.DialogResult = DialogResult.OK; form.Close(); }
                };
                timer.Interval = 250;
                timer.Tick += (sender, args) => update();
                form.Shown += (sender, args) => { later.Select(); update(); timer.Start(); };
                var result = form.ShowDialog();
                timer.Stop();
                return result == DialogResult.OK && CanNotify();
            }
        }
    }
}
