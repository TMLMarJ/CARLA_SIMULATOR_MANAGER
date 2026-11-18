# Changelog

Istotne zmiany projektu są zestawiane w tym pliku. Projekt nie ma jeszcze
oznaczonego wydania produkcyjnego.

## Unreleased

- obsługa CARLA 0.9.15 i 0.9.16;
- GUI dla World & Weather, Actors, Sensors, Simulation Control i Dashboard;
- Movement Control, Sensor Preview i Spectator Preview;
- Configuration Management z kompatybilnym odczytem starszych plików;
- synchronizacja wielu klientów, ownership i Traffic Manager lifecycle;
- przenośne ścieżki, dialogi, scroll oraz procesy preview dla Windows/Linux;
- instrukcje Windows, Linux i WSL2 oraz uporządkowana dokumentacja techniczna.
- `pyproject.toml`, rozdzielone profile CARLA i powtarzalny `uv.lock`, z
  zachowaniem fallbacku pip w `requirements.txt`.

Szczegółowa historia etapów implementacji jest przechowywana w
[archiwum dokumentacji](docs/archive/README.md).
