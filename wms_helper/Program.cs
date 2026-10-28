// midiroomsports_wms: MIDIRooms helper on Windows MIDI Services (LOOP transport).
// Same stdin/stdout protocol as the old midiroomsports.exe:
//   CREATE|name  SEND|name|90 3C 64  CLOSE|name  EXIT      -> OK / OK exists / ERR ...
// For every port "name" a loopback pair is created:
//   "<name>"       (B side)  -> the DAW opens this one as MIDI input
//   "<name> (tx)"  (A side)  -> this helper writes into it
using System.Text;
using Microsoft.Windows.Devices.Midi2;
using Microsoft.Windows.Devices.Midi2.Messages;
using Microsoft.Windows.Devices.Midi2.Endpoints.Loopback;

const string TxSuffix = " (tx)";
const int MaxName = 31; // WinMM port name limit

var logPath = Path.Combine(AppContext.BaseDirectory, "midiroomsports_wms.log");
void Log(string s)
{
    try { File.AppendAllText(logPath, $"{DateTime.Now:HH:mm:ss.fff} {s}{Environment.NewLine}"); } catch { }
}

var stdout = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
void Reply(string s) => stdout.WriteLine(s);

using var initializer = Microsoft.Windows.Devices.Midi2.Initialization.MidiDesktopAppSdkInitializer.Create();
if (!initializer.InitializeSdkRuntime()) { Log("InitializeSdkRuntime failed"); Reply("ERR init sdk runtime"); return 1; }
if (!initializer.EnsureServiceAvailable()) { Log("EnsureServiceAvailable failed"); Reply("ERR midi service not available"); return 1; }

using var session = MidiSession.Create("MIDIRooms");
if (session == null) { Log("MidiSession.Create failed"); Reply("ERR session"); return 1; }

// remove pairs left behind by a previous crash (recognised by the " (tx)" suffix)
int SweepOrphans()
{
    var found = new HashSet<Guid>();
    foreach (var ep in MidiEndpointDeviceInformation.FindAll())
    {
        if (ep.GetTransportSuppliedInfo().TransportId != MidiLoopbackEndpointManager.TransportId) continue;
        if (!ep.Name.EndsWith(TxSuffix, StringComparison.OrdinalIgnoreCase)) continue;
        var g = MidiLoopbackEndpointManager.GetAssociationId(ep);
        if (g != Guid.Empty) found.Add(g);
    }
    foreach (var g in found)
    {
        var ok = MidiLoopbackEndpointManager.RemoveTransientLoopbackEndpoints(new MidiLoopbackEndpointRemovalConfig(g));
        Log($"sweep orphan {g} ok={ok}");
    }
    return found.Count;
}

try { Log($"sweep removed {SweepOrphans()} orphan pair(s)"); } catch (Exception e) { Log("sweep failed: " + e.Message); }

var ports = new Dictionary<string, Port>(StringComparer.OrdinalIgnoreCase);

void RemoveAll()
{
    foreach (var p in ports.Values)
    {
        try
        {
            var ok = MidiLoopbackEndpointManager.RemoveTransientLoopbackEndpoints(new MidiLoopbackEndpointRemovalConfig(p.Assoc));
            Log($"remove {p.Name} ok={ok}");
        }
        catch (Exception e) { Log($"remove {p.Name} failed: {e.Message}"); }
    }
    ports.Clear();
}

Log("READY");
Reply("READY");

string? line;
while ((line = Console.ReadLine()) != null)
{
    line = line.Trim();
    if (line.Length == 0) continue;
    Log("RX " + line);

    var parts = line.Split('|', 3);
    var cmd = parts[0].ToUpperInvariant();

    try
    {
        switch (cmd)
        {
            case "CREATE":
            {
                if (parts.Length < 2 || parts[1].Length == 0) { Reply("ERR usage CREATE|name"); break; }
                var name = parts[1].Trim();
                if (name.Length > MaxName) name = name.Substring(0, MaxName);
                if (ports.ContainsKey(name)) { Reply("OK exists"); break; }

                var txBase = name.Length > MaxName - TxSuffix.Length ? name.Substring(0, MaxName - TxSuffix.Length) : name;
                var assoc = Guid.NewGuid();
                var uid = new string(Guid.NewGuid().ToString().Where(char.IsAsciiLetterOrDigit).ToArray());

                var cfg = new MidiLoopbackEndpointCreationConfig();
                cfg.AssociationId = assoc;
                cfg.EndpointDefinitionA = new MidiLoopbackEndpointDefinition() { Name = txBase + TxSuffix, UniqueId = uid };
                cfg.EndpointDefinitionB = new MidiLoopbackEndpointDefinition() { Name = name, UniqueId = uid };

                var res = MidiLoopbackEndpointManager.CreateTransientLoopbackEndpoints(cfg);
                if (!res.Success) { Log($"CREATE {name} failed: {res.ErrorInformation}"); Reply("ERR create failed"); break; }

                var conn = session.CreateEndpointConnection(res.EndpointDeviceIdA);
                if (conn == null || !conn.Open())
                {
                    Log($"CREATE {name}: cannot open connection");
                    MidiLoopbackEndpointManager.RemoveTransientLoopbackEndpoints(new MidiLoopbackEndpointRemovalConfig(assoc));
                    Reply("ERR open connection");
                    break;
                }

                ports[name] = new Port(name, assoc, conn);
                Log($"CREATE {name} ok");
                Reply("OK");
                break;
            }

            case "SEND":
            {
                if (parts.Length < 3) { Reply("ERR usage SEND|name|hex"); break; }
                if (!ports.TryGetValue(parts[1].Trim(), out var port)) { Reply("ERR unknown port"); break; }

                var err = port.SendHex(parts[2]);
                if (err == null) Reply("OK");
                else { Log($"SEND {parts[1]} {parts[2]} -> {err}"); Reply("ERR " + err); }
                break;
            }

            case "CLOSE":
            {
                if (parts.Length < 2) { Reply("ERR usage CLOSE|name"); break; }
                if (ports.Remove(parts[1].Trim(), out var port))
                {
                    var ok = MidiLoopbackEndpointManager.RemoveTransientLoopbackEndpoints(new MidiLoopbackEndpointRemovalConfig(port.Assoc));
                    Log($"CLOSE {port.Name} remove ok={ok}");
                }
                Reply("OK");
                break;
            }

            case "EXIT":
            {
                RemoveAll();
                Log("EXIT");
                Reply("OK");
                Environment.Exit(0);
                return 0;
            }

            default:
                Reply("ERR unknown command");
                break;
        }
    }
    catch (Exception e)
    {
        Log($"EXC in {cmd}: {e}");
        Reply("ERR " + e.Message.Replace('\n', ' ').Replace('\r', ' '));
    }
}

// stdin closed (the Python app exited): clean up
RemoveAll();
return 0;

sealed class Port
{
    public string Name { get; }
    public Guid Assoc { get; }
    readonly MidiEndpointConnection _conn;

    public Port(string name, Guid assoc, MidiEndpointConnection conn) { Name = name; Assoc = assoc; _conn = conn; }

    public string? SendHex(string hex)
    {
        var tokens = hex.Split(new[] { ' ', ',', '\t' }, StringSplitOptions.RemoveEmptyEntries);
        if (tokens.Length < 1 || tokens.Length > 3) return "bad length";

        var b = new byte[tokens.Length];
        for (int i = 0; i < tokens.Length; i++)
        {
            var t = tokens[i];
            if (t.StartsWith("0x", StringComparison.OrdinalIgnoreCase)) t = t.Substring(2);
            if (!byte.TryParse(t, System.Globalization.NumberStyles.HexNumber, null, out b[i])) return "bad hex";
        }

        int status = b[0] & 0xF0;
        if (status < 0x80 || status > 0xE0) return "unsupported status";

        byte d1 = b.Length > 1 ? b[1] : (byte)0;
        byte d2 = b.Length > 2 ? b[2] : (byte)0;

        var ump = MidiMessageBuilder.BuildMidi1ChannelVoiceMessage(
            0, new MidiGroup(0),
            (Midi1ChannelVoiceMessageStatus)(status >> 4),
            new MidiChannel((byte)(b[0] & 0x0F)), d1, d2);

        var result = _conn.SendSingleMessagePacket((IMidiUniversalPacket)ump);
        return MidiEndpointConnection.SendMessageSucceeded(result) ? null : "send failed " + result;
    }
}
