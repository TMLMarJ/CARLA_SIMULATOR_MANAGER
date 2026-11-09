# Dokumentacja CARLA Simulator Manager

Dokumentacja użytkowa: [README](../README.md) oraz [instalacja](../INSTALLATION.md).

## Aktualna dokumentacja architektury

Poniższe dokumenty opisują bieżący kod źródłowy:

- [Przegląd architektury](architecture/OVERVIEW.md) — cel aplikacji, technologie, komponenty i główne założenia.
- [Struktura plików i katalogów](PROJECT_STRUCTURE.md) — skrócone drzewo repozytorium oraz przeznaczenie najważniejszych modułów.
- [Moduły i zależności](architecture/MODULES.md) — katalog modułów Python, klasy, zależności i problemy do refaktoryzacji.
- [Komunikacja z CARLA](architecture/CARLA_COMMUNICATION.md) — połączenie, polecenia, wątki, procesy, synchronizacja i błędy.
- [Przepływ danych](architecture/DATA_FLOW.md) — źródła prawdy, `DataStorage` oraz zapis i odczyt scenariuszy.
- [Architektura funkcjonalności](architecture/FEATURES.md) — GUI, aktorzy, sensory, ruch, symulacja, scenariusze i podglądy.
- [Plan i historia refaktoryzacji](architecture/legacy/REFACTOR_PLAN.md) — mapa migracji, ryzyka i zrealizowane etapy 4B.1–4B.9.

Historyczne dokumenty projektowe są zachowane w [archiwum decyzji](architecture/legacy/README.md). Mogą zawierać uzasadnienia wcześniejszych decyzji, lecz nie są źródłem prawdy o aktualnej implementacji.

## Pozostałe katalogi

- `architecture/` — wersjonowana dokumentacja techniczna.
- `testing/` — wersjonowana dokumentacja organizacji i pokrycia testów.
- `implementation/` — lokalne raporty implementacyjne Codexa, ignorowane przez Git i nieobecne w zdalnym repozytorium.

## Testy

- [Organizacja i uruchamianie testów jednostkowych](testing/UNIT_TESTS.md)
- [Audyt pokrycia funkcjonalnego](testing/TEST_COVERAGE.md)
- [Katalog testów](../tests/)

Workflow projektu pozostaje w [WORKFLOW.md](../WORKFLOW.md).
