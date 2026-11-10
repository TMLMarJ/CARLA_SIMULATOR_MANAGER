# Testy jednostkowe

## Framework i zakres

Projekt używa `unittest` ze standardowej biblioteki Pythona oraz `unittest.mock`. Zestaw zawiera 33 pliki testowe i 316 unikalnych metod `test_*`.

Testy nie uruchamiają serwera CARLA. Obiekty świata, aktorów, blueprintów, Traffic Managera i kontrolerów są zastępowane lokalnymi fake'ami, `SimpleNamespace` albo mockami. Część testów obejmuje kilka współpracujących modułów i ma charakter testów komponentowych, ale nadal działa w jednym procesie bez zewnętrznego symulatora.

## Struktura katalogu

```text
tests/
├── actors/       # lifecycle aktorów, ownership, blueprinty i ghost/static
├── connection/   # connect, błędy połączenia, disconnect i reset stanu
├── core/         # infrastruktura niezależna od domeny
├── movement/     # sterowanie, cele, nawigacja i podgląd ruchu
├── scenarios/    # zapis, walidacja, preflight, clear i odtwarzanie
├── sensors/      # lifecycle sensorów, ownership guards i cleanup preview
├── simulation/   # WorldSettings, tick scheduler i dashboard
└── world/        # synchronizacja świata i podgląd mapy
```

### Pliki według obszaru

| Katalog | Pliki | Unikalne przypadki |
|---|---:|---:|
| `actors/` | 5 | 40 |
| `connection/` | 1 | 5 |
| `core/` | 5 | 25 |
| `movement/` | 7 | 70 |
| `scenarios/` | 8 | 113 |
| `sensors/` | 1 | 7 |
| `simulation/` | 4 | 35 |
| `world/` | 2 | 21 |
| **Razem** | **33** | **316** |

## Uruchamianie

Polecenia należy wykonywać z katalogu głównego projektu, używając interpretera skonfigurowanego dla projektu w PyCharm.

Cały zestaw:

```powershell
<python> -m unittest discover -s tests -t . -v
```

Wybrany obszar:

```powershell
<python> -m unittest discover -s tests/movement -t . -v
```

Pojedynczy moduł:

```powershell
<python> -m unittest tests.movement.test_vehicle_control -v
```

Pojedyncza klasa lub metoda:

```powershell
<python> -m unittest tests.simulation.test_simulation_manager.SimulationManagerTests -v
```

`tests/` i wszystkie podkatalogi zawierają `__init__.py`. Parametr `-t .` zapewnia stabilne nazwy pakietowe i poprawne importy współdzielonych fake'ów.

## Zależności środowiskowe

Testy korzystają z tego samego interpretera co aplikacja. Do zaimportowania całego zestawu potrzebne są:

- CARLA Python API (`carla`) — część modułów produkcyjnych importuje je na poziomie modułu;
- Pillow — testy `MapPreviewCache` i `MapPreviewGenerator`;
- PyYAML — scenariusze YAML;
- NumPy, Matplotlib i Tkinter — wymagane przez importowane moduły GUI i podglądów;
- pozostałe zależności importowane przez aplikację.

Pygame nie jest uruchamiany przez obecne testy. Import Pygame w procesach podglądu jest opóźniony do funkcji procesu potomnego. Testy nie tworzą `tk.Tk()` ani prawdziwych okien, więc nie wymagają aktywnego pulpitu graficznego w obecnym zakresie.

Po etapie 4B.10 usunięto trzy testy sprawdzające wyłącznie zgodność starych shimów; pozostałe 316 przypadków zachowuje testy funkcjonalne. Końcowy discovery zakończył się wynikiem `OK` dla wszystkich 316 testów. Jednorazowy probe Windows potwierdził uruchomienie top-level targetu z `preview.movement` przez kontekst `multiprocessing.spawn`; nie otwierał Pygame ani połączenia CARLA.

Nie wszystkie zależności importowane przez aplikację są obecnie zadeklarowane w `requirements.txt`; opisano to również w dokumentacji architektury.

## Testy jednostkowe i komponentowe

### Jednostkowe

Do testów jednostkowych należą przede wszystkim:

- lifecycle `Manager` dla connect/error/disconnect z kontrolerem zastąpionym mockiem;
- publiczne guardy i kolejki operacji sensorów;
- konwersje, walidatory i serializer scenariuszy;
- `SimulationManager`;
- `MapPreviewGenerator` i `MapPreviewCache`;
- funkcje wyboru blueprintów i celu ruchu;
- `ManualControlSmoother` i struktury stanu sterowania;
- formatowanie dashboardu.

### Komponentowe bez serwera

Testy komponentowe łączą kilka klas z fake'ami CARLA, np.:

- Save → Load → Save scenariusza;
- preflight i restoration z selective clear;
- pętlę sterowania `CarlaController` z fake world/actor;
- kontrakt składu `CarlaController` z trzema kanonicznymi klasami runtime i brak zdublowanych definicji handlerów;
- lifecycle ruchu podczas zmiany świata;
- synchronizację świata z `DataStorage`;
- kaskadowe usuwanie sensorów rodzica i cleanup rejestrów po błędzie CARLA;
- obsługę GUI przez obiekty utworzone bez prawdziwego root Tkinter.

### Integracyjne z CARLA

Repozytorium nie zawiera obecnie automatycznych testów wymagających działającego serwera CARLA. Nie ma również markerów ani osobnego runnera dla takich testów. Ewentualne przyszłe testy live powinny trafić do jawnie oddzielonego katalogu, np. `tests/integration/`, i być domyślnie pomijane bez konfiguracji serwera.

## Zasady mockowania CARLA

1. Test jednostkowy nie powinien tworzyć `carla.Client` ani łączyć się z portem 2000.
2. Fake powinien implementować tylko kontrakt używany przez test, np. `get_world()`, `get_actors()`, `spawn_actor()` albo `apply_control()`.
3. Runtime ID, `world.id`, `type_id`, parent i `is_alive` należy ustawiać jawnie, ponieważ determinują synchronizację oraz zabezpieczenia przed stale data.
4. Dla operacji mutujących warto rejestrować kolejność wywołań i efekty uboczne, a nie odtwarzać całego CARLA API.
5. `unittest.mock.patch` należy stosować w miejscu użycia symbolu, nie w jego module źródłowym.
6. Nie należy dodawać importowanej klasy `unittest.TestCase` jako pomocniczego fixture'a. Loader potraktuje ją jako dodatkowy zestaw i uruchomi testy ponownie.
7. Wspólne fake'i mogą tymczasowo pozostać w wyspecjalizowanym module testowym, ale nowe, szeroko współdzielone obiekty należy przenosić do modułu pomocniczego bez klas `TestCase`, np. `tests/scenarios/fakes.py`.

## Fixture'y i mocki po audycie

Nie ma `conftest.py`, ponieważ frameworkiem nie jest pytest. Fixture'y są tworzone przez `setUp()`, lokalne klasy `Fake*` i funkcje fabrykujące dane.

Największe współdzielenie występuje w scenariuszach: testy importują fake'i z `test_scenario_serialization.py`, `test_scenario_restoration.py` i `test_world_cleaner.py`. Jest to działające rozwiązanie, ale tworzy zależności między plikami testowymi. W etapie 3B warto wydzielić je do `tests/scenarios/fakes.py` lub kilku mniejszych modułów według kontraktu.

Dwa testy ruchu używają setupu z `WalkerControlTests`. Importują teraz moduł jako `walker_test_support`, dzięki czemu `unittest` nie rejestruje ponownie całej klasy testowej.

## Dodawanie nowych testów

1. Wybierz katalog według testowanej funkcjonalności, nie według numeru etapu implementacji.
2. Nazwij plik `test_<zachowanie_lub_moduł>.py` i klasę według odpowiedzialności, bez nazw `stage`, `fix` i `hotfix`.
3. Utrzymuj Arrange–Act–Assert i jeden główny powód niepowodzenia na metodę.
4. Preferuj publiczny kontrakt. Prywatne metody testuj bezpośrednio tylko wtedy, gdy nie da się obserwować krytycznego zachowania przez API publiczne.
5. Dla cleanupu sprawdzaj zarówno wywołanie `stop/destroy`, jak i usunięcie lokalnego stanu.
6. Dla operacji świata dodawaj wariant stale `world.id`, błąd CARLA i częściowe wykonanie.
7. Nie używaj `sleep`; wstrzykuj zegar albo ustawiaj stan czasu jawnie.
8. Po dodaniu testu uruchom najpierw pojedynczy moduł, potem obszar, a pełny zestaw tylko przed zakończeniem zmiany.

## Końcowe testy manualne z CARLA

Aktualny, niepowielany katalog testów live znajduje się w
[MANUAL_E2E_CHECKLIST.md](MANUAL_E2E_CHECKLIST.md). Obejmuje BasicAgent,
Maximum/Adaptive speed, lifecycle mapy, przejścia trybów, reconnect,
managed/external actors, Scenario Save/Load i współpracę wielu klientów.
Testy jednostkowe z fake'ami nie zaliczają tych przypadków.

## Znane ograniczenia organizacji

- `test_scenario_serialization.py` i `test_world_cleaner.py` są nadal duże i łączą testy jednostkowe z komponentowymi.
- Część testów wywołuje prywatne metody `CarlaController` i ręcznie buduje obiekty przez `__new__`, więc jest silnie zależna od implementacji.
- Bufory zdarzeń sensorów mają podstawowy test zamknięcia i późnego callbacku; nadal brakuje pełnych testów wszystkich dekoderów, automatycznego reconnectu i shutdownu rzeczywistych procesów.
- Brak pomiaru pokrycia kodu; liczby w tym dokumencie oznaczają przypadki testowe, nie procent pokrycia.

Szczegółowy audyt luk znajduje się w [TEST_COVERAGE.md](TEST_COVERAGE.md).
