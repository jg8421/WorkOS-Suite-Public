# Android capture smoke fixture

This local-only fixture displays synthetic text. It requests no permissions, has no networking or storage, and never reads notifications. It is separate from the Personal Memory application. All three strings below are artificial test markers, not real secrets.

Build with the existing JDK and Android SDK:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\android-smoke\build-fixture.ps1
```

The script only creates files under this fixture directory. It does not install anything or touch a device. It produces `dist/MemoryCaptureSmoke.apk`; every build has a new debug signing key, so rebuilding requires uninstalling the previous fixture before reinstalling it. Use the same built APK for repeated tests.

Install and start deliberately on a connected test phone, replacing `SERIAL`:

```powershell
adb -s SERIAL install tests\android-smoke\dist\MemoryCaptureSmoke.apk
adb -s SERIAL shell am start -S -n com.personalmemory.smokefixture/.SmokeActivity
adb -s SERIAL shell am start -S -n com.personalmemory.smokefixture/.SmokeActivity --es mode form
adb -s SERIAL shell am start -S -n com.personalmemory.smokefixture/.SmokeActivity --es mode burst
```

The default page has `SMOKE_NEUTRAL_VISIBLE_20260930`, a button that changes `SMOKE_UPDATE_VISIBLE_N`, and a burst of 24 updates at 200 ms intervals. The form page contains an ordinary editable field with `SMOKE_EDITABLE_NEVER_STORE` and a password field with `SMOKE_SECRET_NEVER_STORE_8472`. Both fields must be excluded from captured content. A strict implementation may exclude the entire form page.

## Focused checks

Inspect only events with `metadata.package == "com.personalmemory.smokefixture"`; avoid reading unrelated real app content. Test with a disposable receiver/storage where possible.

| Check | Steps | Expected result |
| --- | --- | --- |
| Opt-in default | Fresh Personal Memory test install, before turning on enhanced collection; open neutral fixture | No accessibility events saved |
| Explicit enabled state | Enable enhanced collection and Android accessibility permission; open neutral page | Fixture app/window and visible neutral marker reach receiver |
| Pause | Pause enhanced collection, then run neutral and burst pages | No new fixture capture after pause takes effect; queued older events are a separate case |
| Editable/password exclusion | Enable collection; open form page, focus/type in each field, return to neutral | Neither `SMOKE_EDITABLE_NEVER_STORE` nor `SMOKE_SECRET_NEVER_STORE_8472` appears in capture or offline queue |
| Changing-content throttle | Run burst page once | Capture count remains bounded by the configured per-package/type throttle, not by each new string |
| Window/package consistency | Alternate fixture and Personal Memory rapidly; include system settings | Fixture markers are never attributed to another package; ignored windows reset app-duration tracking |
| Offline queue and retry | Use disposable receiver, make it unreachable, open neutral/burst, restore it without creating more events | Phone remains responsive; queued test events arrive automatically once reachable, without duplicate records |
| Queue crash durability | With receiver unavailable, generate a fixture event, restart Personal Memory, restore receiver | Previously queued event survives; unique source IDs make retries idempotent |

Remove the fixture when finished:

```powershell
adb -s SERIAL uninstall com.personalmemory.smokefixture
```

Package exclusions, browser private-mode detection, and password heuristics also need direct unit checks of the pure filtering helper; one UI fixture cannot establish universal coverage across Android apps.

Run those local filtering/throttling checks without Android or a device:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\android-smoke\check-privacy.ps1
```
