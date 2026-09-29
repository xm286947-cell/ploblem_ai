# ADR-003 Cross-Platform Release
STATUS=ACCEPTED

Decision:
- One source/package baseline for Windows and macOS.
- BAT and SH are wrappers only; common Python precheck/startup owns behavior.
- Add `.command` on macOS as a thin delegating wrapper.
- Same app factory, routes, data model, contracts and dependency resolution on both OSes.
- Package manifest binds source SHA, file hashes, contract versions and launchers.
- OS-specific business forks are forbidden.
