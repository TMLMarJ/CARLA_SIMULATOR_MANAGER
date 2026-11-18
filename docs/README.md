# Dokumentacja CARLA Simulator Manager

Instrukcje dla nowego użytkownika znajdują się w głównym
[README](../README.md). Rozszerzona instalacja Windows jest opisana w
[INSTALLATION.md](../INSTALLATION.md), a Linux i WSL2 w
[LINUX_SETUP.md](LINUX_SETUP.md).

## Dokumenty kanoniczne

- [Architektura](ARCHITECTURE.md)
- [Struktura projektu](PROJECT_STRUCTURE.md)
- [Komunikacja klient–serwer CARLA](CARLA_COMMUNICATION.md)
- [Funkcjonalności GUI](GUI_FUNCTIONALITIES.md)
- [Configuration Management](SCENARIO_MANAGEMENT.md)
- [Movement Control](MOVEMENT_CONTROL.md)
- [Testowanie i weryfikacja](TESTING.md)
- [Audyt kompatybilności Linux](LINUX_COMPATIBILITY_AUDIT.md)
- [Materiał techniczny do pracy inżynierskiej](THESIS_TECHNICAL_REFERENCE.md)
- [Modernizacja zarządzania zależnościami](implementation/DEPENDENCY_MANAGEMENT_MODERNIZATION.md)

Dokumenty w `architecture/` rozwijają wybrane zagadnienia na potrzeby
programistyczne i pracy inżynierskiej:

- [przegląd architektury](architecture/OVERVIEW.md),
- [moduły i zależności](architecture/MODULES.md),
- [komunikacja z CARLA](architecture/CARLA_COMMUNICATION.md),
- [przepływ danych](architecture/DATA_FLOW.md),
- [architektura funkcjonalności](architecture/FEATURES.md).

W razie rozbieżności pierwszeństwo mają aktualny kod i dokumenty kanoniczne.
Techniczne nazwy `scenarios`, `ScenarioManager` i podobne pozostały elementami
wewnętrznego API; funkcja widoczna dla użytkownika nazywa się Configuration
Management.

## Testy i workflow

- [Organizacja testów jednostkowych](testing/UNIT_TESTS.md)
- [Audyt pokrycia funkcjonalnego](testing/TEST_COVERAGE.md)
- [Manualna checklista E2E](testing/MANUAL_E2E_CHECKLIST.md)
- [Katalog testów](../tests/)
- [Workflow projektu](../WORKFLOW.md)

Wyników testów mockowanych nie należy przedstawiać jako zaliczonego E2E z
prawdziwym serwerem CARLA.

## Archiwum

[Archiwum raportów](archive/README.md) zachowuje kontekst wykonanych etapów, ale
nie jest źródłem bieżących instrukcji. Pozostałe lokalne raporty w ignorowanych
przez Git katalogach `docs/implementation/`, `docs/architecture/legacy/` i
`docs/thesis/` mają wyłącznie charakter historyczny.
