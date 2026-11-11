// "Ai Book Reader.exe" of the small portable app: starts python\pythonw.exe AiBookReader.pyw
// with the same arguments. Built by tools/build_lite.py with the C# compiler that comes with
// Windows (.NET Framework 4), so it needs nothing extra and stays a few KB.
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Ai Book Reader")]
[assembly: AssemblyProduct("Ai Book Reader")]
[assembly: AssemblyDescription("Reads your books aloud with local AI voices")]
[assembly: AssemblyCompany("Farathim")]
[assembly: AssemblyVersion("@VERSION@.0")]
[assembly: AssemblyFileVersion("@VERSION@.0")]

static class Launcher
{
    static string Quote(string arg)
    {
        if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
        var sb = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in arg)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') sb.Append('\\', slashes * 2 + 1);
            else sb.Append('\\', slashes);
            slashes = 0;
            sb.Append(c);
        }
        sb.Append('\\', slashes * 2);
        return sb.Append('"').ToString();
    }

    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(dir, "python", "pythonw.exe");
        string script = Path.Combine(dir, "AiBookReader.pyw");
        if (!File.Exists(python) || !File.Exists(script))
        {
            MessageBox.Show("Some files of Ai Book Reader are missing (python\\pythonw.exe or AiBookReader.pyw).\n\n"
                            + "Unpack the whole portable zip again; your data folder can stay.",
                            "Ai Book Reader", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var line = new StringBuilder(Quote(script));
        bool wait = false;
        foreach (string a in args)
        {
            if (a == "--selftest" || a == "--wait") wait = true;
            if (a != "--wait") line.Append(' ').Append(Quote(a));
        }
        var psi = new ProcessStartInfo(python, line.ToString());
        psi.UseShellExecute = false;
        psi.WorkingDirectory = dir;
        try
        {
            using (Process p = Process.Start(psi))
            {
                if (!wait) return 0;
                p.WaitForExit();
                return p.ExitCode;
            }
        }
        catch (Exception e)
        {
            MessageBox.Show("Ai Book Reader could not start:\n\n" + e.Message, "Ai Book Reader",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
