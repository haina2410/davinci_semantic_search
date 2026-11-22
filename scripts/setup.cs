using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;

[assembly: AssemblyTitle("Davinci Semantic Search Setup")]
[assembly: AssemblyProduct("Davinci Semantic Search")]

internal static class Setup
{
    private static int Main(string[] args)
    {
        string staging = Path.Combine(Path.GetTempPath(), "davinci-semantic-search-" + Guid.NewGuid().ToString("N"));
        bool uninstall = args.Length > 0 && args[0].Equals("-Uninstall", StringComparison.OrdinalIgnoreCase);
        int result = 1;

        // PowerShell receives Ctrl+C in the same console and can run its cleanup.
        Console.CancelKeyPress += (sender, e) => { e.Cancel = true; };
        Console.WriteLine("Davinci Semantic Search - " + (uninstall ? "Uninstall" : "Setup"));
        try
        {
            Directory.CreateDirectory(staging);
            string archive = Path.Combine(staging, "payload.zip");
            using (Stream payload = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
            using (FileStream output = File.Create(archive))
            {
                payload.CopyTo(output);
            }
            string package = Path.Combine(staging, "package");
            ZipFile.ExtractToDirectory(archive, package);

            string powershell = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe");
            string script = Path.Combine(package, "scripts", uninstall ? "uninstall.ps1" : "install.ps1");
            var command = new StringBuilder("-NoLogo -NoProfile -ExecutionPolicy Bypass -File ");
            command.Append(QuoteArgument(script));
            for (int i = uninstall ? 1 : 0; i < args.Length; i++)
            {
                command.Append(' ').Append(QuoteArgument(args[i]));
            }

            var startInfo = new ProcessStartInfo(powershell, command.ToString());
            startInfo.UseShellExecute = false;
            using (Process process = Process.Start(startInfo))
            {
                process.WaitForExit();
                result = process.ExitCode;
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
        }
        finally
        {
            try
            {
                Directory.Delete(staging, true);
            }
            catch (DirectoryNotFoundException) { }
            catch (IOException error)
            {
                Console.Error.WriteLine("Could not remove temporary setup files: " + error.Message);
            }
            catch (UnauthorizedAccessException error)
            {
                Console.Error.WriteLine("Could not remove temporary setup files: " + error.Message);
            }
        }

        Console.WriteLine();
        if (result != 0)
        {
            Console.WriteLine(uninstall ? "Uninstall did not complete." : "Setup did not complete.");
        }
        if (!Console.IsInputRedirected)
        {
            Console.Write("Press Enter to close.");
            Console.ReadLine();
        }
        return result;
    }

    private static string QuoteArgument(string value)
    {
        // Double backslashes before a quote or the closing delimiter.
        var quoted = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char character in value)
        {
            if (character == '\\')
            {
                backslashes++;
                continue;
            }
            quoted.Append('\\', character == '"' ? 2 * backslashes + 1 : backslashes);
            quoted.Append(character);
            backslashes = 0;
        }
        return quoted.Append('\\', 2 * backslashes).Append('"').ToString();
    }
}
