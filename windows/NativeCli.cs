// Native argv boundary for the Windows CLI. No shell interpolation or argument files.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

public static class BasaltwaterNativeCli {
    [DllImport("kernel32.dll")] private static extern uint GetConsoleCP();

    public static int Main(string[] args) {
        try {
            string entry = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Invoke-Basaltw.ps1");
            if (!File.Exists(entry)) throw new IOException("CLI entrypoint missing");
            // The fixed bootstrap reads one private stdin packet, then splats
            // strings into the existing script. Project arguments are never code.
            string script = @"
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    $packet = [Convert]::FromBase64String([Console]::In.ReadLine())
    $reader = [IO.BinaryReader]::new([IO.MemoryStream]::new($packet), [Text.UTF8Encoding]::new($false, $true))
    try {
        $count = $reader.ReadInt32()
        if ($count -lt 0 -or $count -gt 32768) { throw 'Invalid CLI argument count' }
        $cliArguments = [string[]]::new($count)
        for ($i = 0; $i -lt $count; $i++) { $cliArguments[$i] = $reader.ReadString() }
    } finally { $reader.Dispose() }
    & '" + entry.Replace("'", "''") + @"' @cliArguments 6>&1 | ForEach-Object { [Console]::Out.WriteLine([string]$_) }
    exit $LASTEXITCODE
} catch { [Console]::Error.WriteLine('basaltw: native argument transport failed'); exit 1 }
";
            byte[] packet;
            using (var stream = new MemoryStream()) {
                using (var writer = new BinaryWriter(stream, new UTF8Encoding(false, true), true)) {
                    writer.Write(args.Length);
                    foreach (string arg in args) writer.Write(arg);
                }
                packet = stream.ToArray();
            }
            var info = new ProcessStartInfo();
            info.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                @"WindowsPowerShell\v1.0\powershell.exe");
            info.Arguments = "-NoLogo -NoProfile -InputFormat Text -OutputFormat Text -ExecutionPolicy Bypass -EncodedCommand " +
                Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
            info.UseShellExecute = false;
            // Preserve console control events in a terminal; allocate no window
            // when invoked from a provider without an attached console.
            info.CreateNoWindow = GetConsoleCP() == 0;
            info.RedirectStandardInput = true;
            info.EnvironmentVariables.Remove("PSModulePath");
            using (var process = Process.Start(info)) {
                process.StandardInput.WriteLine(Convert.ToBase64String(packet));
                process.StandardInput.Close();
                process.WaitForExit();
                return process.ExitCode;
            }
        } catch {
            // Do not echo arguments, environment or exception paths.
            Console.Error.WriteLine("basaltw: native launcher could not complete");
            return 1;
        }
    }
}
