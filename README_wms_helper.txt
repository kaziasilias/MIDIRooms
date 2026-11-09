MIDIRooms - wms_helper (midiroomsports_wms.exe)
================================================
Έκδοση API: Windows.Devices.Midi2 0.99.92-preview.11 (In-box Preview 11)

Τι είναι
--------
Το midiroomsports_wms.exe είναι ο helper του MIDIRooms που δημιουργεί δυναμικά
εικονικά MIDI endpoints (loopback ζεύγη) μέσω των Windows MIDI Services (WMS).
Τρέχει ως υποδιεργασία της εφαρμογής Python και επικοινωνεί μέσω stdin/stdout.
Αντικαθιστά τον παλιό helper που βασιζόταν στο Tobias Erichsen virtualMIDI.

Για κάθε remote MIDI συσκευή δημιουργείται ένα ζεύγος:
  "<όνομα> (DAW)"  -> το ανοίγει το DAW ως MIDI Input
  "<όνομα> (MR)"   -> το ανοίγει η εφαρμογή (ο helper γράφει εδώ)

Αλλαγή σε σχέση με την προηγούμενη έκδοση (rc.4.25)
---------------------------------------------------
Η προηγούμενη έκδοση χρησιμοποιούσε το πακέτο Microsoft.Windows.Devices.Midi2
(1.0.17-rc.4.25) και απαιτούσε εγκατεστημένο το "Windows MIDI Services SDK
Runtime & Tools" σε κάθε υπολογιστή. Το SDK Runtime αυτό έχει καταργηθεί.

Η νέα έκδοση χρησιμοποιεί το API Windows.Devices.Midi2, που θα ενσωματωθεί
στα Windows. Μέχρι τότε, η εφαρμογή κουβαλάει τη δική της αντιγραφή του API
(Windows.Devices.Midi2.dll + .pri) στον φάκελο wms\. Δεν χρειάζεται SDK Runtime.
Όταν τα Windows αποκτήσουν δική τους αντιγραφή, χρησιμοποιείται αυτόματα
εκείνη, χωρίς αλλαγή στον κώδικα.


1. Προαπαιτούμενα για ΜΕΤΑΓΛΩΤΤΙΣΗ (μηχάνημα ανάπτυξης)
-------------------------------------------------------
- Windows 11 25H2 ή νεότερο
- .NET 10 SDK  (έλεγχος:  dotnet --list-sdks   -> πρέπει να εμφανίζεται 10.x)
- Το αρχείο Windows.Devices.Midi2.0.99.92-preview.11.nupkg
  (από τα Assets του release "In-box Preview 11":
   https://github.com/microsoft/MIDI/releases/tag/inbox-preview-11)
- Πρόσβαση στο internet την πρώτη φορά (restore του πακέτου
  Microsoft.Windows.CsWinRT από το nuget.org)
- ΔΕΝ χρειάζεται το Windows MIDI Services SDK Runtime & Tools.


2. Αρχεία του project
---------------------
Φάκελος wms_helper\ με:
  Program.cs                  ο κώδικας του helper
  midiroomsports_wms.csproj   ορισμοί project
                              (net10.0-windows10.0.26100.0,
                               AssemblyName = midiroomsports_wms,
                               PackageReference Windows.Devices.Midi2 0.99.92-preview.11,
                               PackageReference Microsoft.Windows.CsWinRT 2.2.0)
  nuget.config                δηλώνει τον φάκελο nuget-local ως πηγή πακέτων
  nuget-local\
    Windows.Devices.Midi2.0.99.92-preview.11.nupkg
                              το πακέτο ΟΠΩΣ ΚΑΤΕΒΗΚΕ (όχι αποσυμπιεσμένο)

Σημειώσεις:
- Μην κάνετε extract το .nupkg μέσα στον wms_helper. Το NuGet χρειάζεται το
  ίδιο το .nupkg, και τα αποσυμπιεσμένα αρχεία μπορεί να μπλέξουν το build.
- Οι εκδόσεις των πακέτων είναι σταθερές (όχι "*-*"), ώστε το build να είναι
  επαναλήψιμο και να μην επιλεγεί κατά λάθος το παλιό rc4 πακέτο.
- Το πακέτο δεν περιέχει έτοιμο .NET projection. Το CsWinRT το παράγει κατά
  το build από το Windows.Devices.Midi2.winmd (ρυθμίσεις CSWinRTInputs /
  CsWinRTIncludes στο .csproj).


3. Μεταγλώττιση
---------------
Στο Command Prompt:

  cd <διαδρομή>\wms_helper
  dotnet nuget locals all --clear      (μόνο αν υπάρχει παλιά cache πακέτων)
  dotnet publish -c Release -r win-x64 -o ..\wms_new

Το αποτέλεσμα βρίσκεται στον φάκελο wms_new\, δίπλα στο midirooms.py
(έξω από τον wms_helper), και περιέχει:

  midiroomsports_wms.exe                  εκκινητής
  midiroomsports_wms.dll                  ο κώδικας + το projection του CsWinRT
  midiroomsports_wms.deps.json
  midiroomsports_wms.runtimeconfig.json
  Windows.Devices.Midi2.dll               το API (x64)
  Windows.Devices.Midi2.pri               resources του API (απαραίτητο)
  WinRT.Runtime.dll
  Microsoft.Windows.SDK.NET.dll
  (*.pdb: αρχεία debug, προαιρετικά)

Μην βάζετε τον φάκελο εξόδου μέσα στον wms_helper.


4. Τοποθέτηση στην εφαρμογή
---------------------------
Η εφαρμογή Python ψάχνει τον helper στο:
  <φάκελος του midirooms.py>\wms\midiroomsports_wms.exe

  1. Κρατήστε αντίγραφο του τωρινού wms\ (π.χ. wms_backup\).
  2. Διαγράψτε τον παλιό wms\.
  3. Μετονομάστε τον wms_new\ σε wms\.

Χρειάζεται ΟΛΟ το περιεχόμενο του φακέλου, όχι μόνο το .exe. Το
Windows.Devices.Midi2.dll και το .pri πρέπει να βρίσκονται δίπλα στο .exe.


5. Προαπαιτούμενα για ΕΚΤΕΛΕΣΗ (κάθε μηχάνημα που τρέχει την εφαρμογή)
---------------------------------------------------------------------
  a) Windows 11 25H2 ή νεότερο, με τις τελευταίες ενημερώσεις
     (περιέχουν την υπηρεσία Windows MIDI Services και το loopback transport).
     Το Windows 11 24H2 δεν υποστηρίζεται.
  b) .NET 10 Runtime (x64), εκτός αν το build έγινε self-contained.
  c) ΔΕΝ χρειάζεται το Windows MIDI Services SDK Runtime & Tools.
     Αν υπάρχει εγκατεστημένο το παλιό rc4 SDK Runtime & Tools, η Microsoft
     συνιστά να απεγκατασταθεί από Settings > Apps > Installed Apps.
  d) Τα πακέτα Tools / Transports των νέων previews είναι προαιρετικά.
     Δεν τα χρειάζεται ο helper. Το DLL που εγκαθιστούν είναι μόνο για τις
     δικές τους εφαρμογές και δεν πρέπει να χρησιμοποιείται από το MIDIRooms.
Αν λείπει κάτι, ο helper τερματίζει αμέσως ή απαντά με σφάλμα (βλ. ενότητα 8).


6. Παράμετροι και μεταβλητές
----------------------------
  --nosweep              ο helper ΔΕΝ καθαρίζει ορφανά ζεύγη κατά την εκκίνηση.
  MIDIROOMS_NOSWEEP=1    μεταβλητή περιβάλλοντος της εφαρμογής Python. Όταν
                         υπάρχει, η εφαρμογή ξεκινά τον helper με --nosweep.
Χωρίς αυτά, κατά την εκκίνηση διαγράφονται όλα τα ζεύγη με κατάληξη " (MR)"
από προηγούμενα crashes. ΠΡΟΣΟΧΗ: αν τρέχουν δύο helpers ή ένα DAW έχει ανοιχτό
ένα τέτοιο endpoint, ο καθαρισμός μπορεί να το διαγράψει. Για δεύτερη
ταυτόχρονη εκτέλεση χρησιμοποιήστε --nosweep.


7. Πρωτόκολλο stdin/stdout (για δοκιμή χωρίς την εφαρμογή)
-----------------------------------------------------------
Μία εντολή ανά γραμμή. Απαντήσεις: OK / OK exists / ERR <αιτία>.

  CREATE|όνομα          δημιουργεί το ζεύγος (όνομα έως 31 χαρακτήρες)
  SEND|όνομα|90 3C 64   στέλνει MIDI 1.0 channel voice μήνυμα (1-3 bytes)
  CLOSE|όνομα           διαγράφει το ζεύγος
  EXIT                  καθαρίζει όλα τα ζεύγη και τερματίζει

Κατά την εκκίνηση ο helper γράφει "READY" όταν είναι έτοιμος.
Αν κλείσει το stdin (π.χ. τερματίσει η εφαρμογή Python), ο helper διαγράφει
όλα τα ζεύγη που δημιούργησε και τερματίζει.
Παράδειγμα (Command Prompt, μέσα στον φάκελο wms):
  midiroomsports_wms.exe
  CREATE|test
  SEND|test|90 3C 64
  EXIT


8. Αρχείο καταγραφής και αντιμετώπιση προβλημάτων
-------------------------------------------------
Ο helper γράφει στο midiroomsports_wms.log (στον ίδιο φάκελο με το exe).

  "API system provided=False" (στην αρχή του log)
        -> κανονικό: χρησιμοποιείται η τοπική αντιγραφή του API από τον
           φάκελο wms. Με "True" χρησιμοποιείται η αντιγραφή των Windows.
  "ERR midi service not available" / "EnsureServiceAvailable failed"
        -> τα Windows δεν είναι αρκετά πρόσφατα ή το WMS δεν είναι ενεργό
           (π.χ. Legacy API Mode).
  "ERR loopback transport not available"
        -> η υπηρεσία δεν έχει το loopback transport: ενημερώστε τα Windows.
  Δεν δημιουργείται καθόλου log / το exe κλείνει αμέσως
        -> συνήθως λείπει το .NET 10 Runtime, ή λείπει το
           Windows.Devices.Midi2.dll / .pri από τον φάκελο wms.
  "CREATE ... failed: <κωδικός> <μήνυμα>"
        -> δείτε τον κωδικό στο log (π.χ. endpoint που υπάρχει ήδη).
           Αν μείνουν ζεύγη από crash, ένα restart της υπηρεσίας MIDI ή
           του υπολογιστή τα καθαρίζει.
  "ERR init sdk runtime"
        -> τρέχει ακόμη ο ΠΑΛΙΟΣ helper (rc4). Αντικαταστήστε τον φάκελο wms
           με το νέο build (ενότητα 4).

Σφάλματα κατά τη μεταγλώττιση:
  "Unable to find package Windows.Devices.Midi2"
        -> λείπει το nuget.config ή το .nupkg από τον φάκελο nuget-local.
  Σφάλματα για Microsoft.Windows.Devices.Midi2 / namespaces που δεν υπάρχουν
        -> διαγράψτε τους φακέλους bin και obj και ξανατρέξτε το publish.

Χρήσιμοι έλεγχοι:
  winver                     (Windows 11 25H2 ή νεότερο)
  dotnet --list-runtimes     (πρέπει να υπάρχει Microsoft.NETCore.App 10.x)
  dotnet list package        (μέσα στον φάκελο wms_helper)


9. Γνωστοί περιορισμοί
----------------------
- Το SEND υποστηρίζει μόνο channel voice μηνύματα (status 0x80-0xEF, 1-3 bytes).
  Δεν υποστηρίζονται SysEx και system messages.
- Το όνομα endpoint περιορίζεται στους 31 χαρακτήρες (όριο WinMM).
  Το " (DAW)"/" (MR)" προστίθεται μέσα σε αυτό το όριο.
- Το API είναι ακόμη σε preview. Η Microsoft επιτρέπει τη διανομή του
  Windows.Devices.Midi2.dll / .winmd / .pri μαζί με την εφαρμογή (άδεια
  "go live" του Preview 11), όχι σε εκδόσεις κάτω από Windows 11 25H2.
  Όταν το API ενσωματωθεί στα Windows, η τοπική αντιγραφή μπορεί να αφαιρεθεί.
