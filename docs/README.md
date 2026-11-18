# Dokumentacja CARLA Simulator Manager

Dokumentacja użytkowa: [README](../README.md) oraz [instalacja](../INSTALLATION.md).

Dokumenty kanoniczne bieżącego kodu roboczego:

- [Architektura](ARCHITECTURE.md)
- [Komunikacja z CARLA](CARLA_COMMUNICATION.md)
- [Funkcjonalności GUI](GUI_FUNCTIONALITIES.md)
- [Configuration Management](SCENARIO_MANAGEMENT.md)
- [Movement Control](MOVEMENT_CONTROL.md)
- [Audyt kompatybilności Linux](implementation/LINUX_COMPATIBILITY_AUDIT.md)
- [Instalacja Linux — wymaga smoke testu środowiska](LINUX_SETUP.md)
- [Raport implementacji Windows/Linux](implementation/LINUX_COMPATIBILITY_IMPLEMENTATION.md)
- [Testowanie](TESTING.md)
- [Materiał techniczny do pracy inżynierskiej](thesis/THESIS_TECHNICAL_REFERENCE.md)
- [Raport przeglądu](../DOCUMENTATION_REVIEW_REPORT.md)

W razie rozbieżności dokumenty kanoniczne i aktualny kod mają pierwszeństwo.
Raporty etapowe zachowują historię implementacji, ale mogą opisywać wcześniejsze
wartości domyślne i nie są instrukcją uruchomieniową.

## Aktualna dokumentacja architektury

Poniższe dokumenty opisują bieżący kod źródłowy:

- [Przegląd architektury](architecture/OVERVIEW.md) — cel aplikacji, technologie, komponenty i główne założenia.
- [Struktura plików i katalogów](PROJECT_STRUCTURE.md) — skrócone drzewo repozytorium oraz przeznaczenie najważniejszych modułów.
- [Moduły i zależności](architecture/MODULES.md) — katalog modułów Python, klasy, zależności i problemy do refaktoryzacji.
- [Komunikacja z CARLA](architecture/CARLA_COMMUNICATION.md) — połączenie, polecenia, wątki, procesy, synchronizacja i błędy.
- [Przepływ danych](architecture/DATA_FLOW.md) — źródła prawdy, `DataStorage` oraz zapis i odczyt scenariuszy.
- [Architektura funkcjonalności](architecture/FEATURES.md) — GUI, aktorzy, sensory, ruch, symulacja, scenariusze i podglądy.
- [Plan i historia refaktoryzacji](architecture/legacy/REFACTOR_PLAN.md) — archiwalna mapa migracji i zrealizowanych etapów.

Historyczne dokumenty projektowe są zachowane w [archiwum decyzji](architecture/legacy/README.md). Mogą zawierać uzasadnienia wcześniejszych decyzji, lecz nie są źródłem prawdy o aktualnej implementacji.

## Pozostałe katalogi

- `architecture/` — wersjonowana dokumentacja techniczna.
- `testing/` — wersjonowana dokumentacja organizacji i pokrycia testów.
- `implementation/` — lokalne raporty implementacyjne Codexa, ignorowane przez Git i nieobecne w zdalnym repozytorium.

## Testy

- [Organizacja i uruchamianie testów jednostkowych](testing/UNIT_TESTS.md)
- [Audyt pokrycia funkcjonalnego](testing/TEST_COVERAGE.md)
- [Manualna checklista E2E](testing/MANUAL_E2E_CHECKLIST.md)
- [Katalog testów](../tests/)

Workflow projektu pozostaje w [WORKFLOW.md](../WORKFLOW.md).

Repozytorium nie zawiera obecnie arkusza `.xlsx`/`.xls`. Historyczne raporty z
celowanych przebiegów nie są wynikiem manualnych E2E.
