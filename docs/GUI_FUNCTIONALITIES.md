# Funkcjonalności GUI

## Zasady wspólne

`gui.main_view.MainView` buduje pięć głównych zakładek. Widoki zależne od CARLA
są aktywowane dopiero po Connect. Callbacki GUI zlecają pracę
`CarlaController`; wyniki wracają przez `DataStorage` i kolejki zdarzeń.

Rekordy mają ownership `Managed`, `External` albo `Internal`. Mutacje są
przeznaczone dla `Managed`. `Take Ownership` nie respawnuje aktora, lecz przed
adopcją sprawdza jego bieżącą tożsamość. Nazwy logiczne Managed actors i sensors
są globalnie unikalne; resolver dodaje najmniejszy wolny sufiks `_1`, `_2`, itd.

## Main View

Implementacja: `gui/tabs/home.py`, `gui/widgets/dashboard.py`.

### Connection Manager

- host i port serwera, domyślnie `127.0.0.1:2000`;
- Connect, Disconnect i stan połączenia;
- polling wyniku workera bez blokowania Tkintera.

Po Connect tworzone są zakładki świata/aktorów/sensorów. Disconnect kończy
lokalne runtime, podglądy, workera i tworzy nowy store. GUI nie uruchamia CARLA.

### Dashboard

Pokazuje migawkę symulacji, liczby aktorów i sensorów, ownership, Ego vehicles,
stan kolejek, aplikacji, Spectatora i ostatnie błędy. Dane przygotowuje
`simulation.dashboard.SimulationDashboardMonitor`; nie są one niezależnym
źródłem prawdy ani historią telemetryczną.

Delta Time pochodzi z bieżącego `WorldSnapshot`. Simulation FPS jest niezależnym
pomiarem przyrostu `WorldSnapshot.frame` względem lokalnego zegara monotonicznego
w kroczącym oknie jednej sekundy. Działa dla fixed/variable step oraz sync/async,
uwzględnia kilka ticków między odczytami i po zatrzymaniu postępu spada do 0 FPS.
Pierwsza próbka po Connect, zmianie episode lub resecie ma wartość `N/A`. Nie jest
to Rendering FPS. Monitor wykorzystuje istniejący odczyt snapshotu co 250 ms.

### Spectator Preview

Użytkownik ustawia rozdzielczość, FOV i FPS, po czym uruchamia niezależne okno
Pygame. Proces `preview.spectator` tworzy osobny `carla.Client`, sensor RGB i
steruje `world.get_spectator()`. Obsługuje ruch klawiaturą/myszą, prędkość i
boost. Kończy się przy zmianie episode, Disconnect lub timeoutie cleanupu.

Ograniczenia: dodatkowy klient i sensor zwiększają obciążenie; wymagane są
Pygame, renderowanie oraz możliwość otwarcia okna. To nie jest zapis wideo.

## World & Weather

Implementacja: `gui/tabs/world.py`, `services/world.py`,
`services/weather.py`, `preview/map.py`.

### Mapy

Lista pochodzi z `client.get_available_maps()`. `visible_map_names()` ukrywa
tylko pozycję, której znormalizowany końcowy segment to `EmptyMap`; nie usuwa
ostatniej pozycji i nie filtruje `MyEmptyMap`. Pełna lista pozostaje w
`DataStorage.world_lib`, więc Configuration Load może wskazać `EmptyMap`.

Zmiana mapy wywołuje `load_world()`, zatrzymuje zasoby starego episode, pobiera
świeży `World` i instaluje nowy cache. Operacja oddziałuje na wszystkich
klientów serwera. Podgląd mapy jest generowany z OpenDRIVE i buforowany w
katalogu cache użytkownika (`LocalAppData` na Windows, XDG Cache lub `~/.cache`
na Linuxie). Dotychczasowe obrazy z `resources/maps/generated/` i
`images/maps/generated/` pozostają odczytywane; brak obrazu nie blokuje świata.

### Pogoda

Preset combobox jest budowany dynamicznie z instancji
`carla.WeatherParameters`. Dostępne są presety, custom values, Apply, Refresh i
Reset. Kod nie zakłada identycznej listy presetów w 0.9.15 i 0.9.16.

### Refresh World

Przycisk odczytuje świeży `client.get_world()`:

- ten sam `world_id` — odświeża inventory;
- inny `world_id` — pełna odbudowa świata, cache i zakładek; stare Movement i
  Spectator Preview są zamykane.

Nie wykonuje Disconnect/Reconnect i nie odtwarza aktorów Managed starego
episode. Szczegóły: [CARLA_COMMUNICATION.md](CARLA_COMMUNICATION.md).

## Actors — Vehicles

Implementacja: `gui/tabs/vehicles.py`, `gui/windows/actor_editor.py`,
`services/actors.py`, `models/actors.py`.

Funkcje:

- wybór blueprintu i spawn pointu, ghost preview przed spawnem;
- logical name, atrybuty blueprintu, kolor i `role_name` zależnie od blueprintu;
- oznaczenie Ego przez `role_name=hero`; wiele Ego jest dozwolone;
- tabela z ID, nazwą, modelem, ownership i znacznikiem Ego;
- Rename, transform, physics, Change Model, Remove, Refresh, Take Ownership;
- otwarcie Movement Control dla Managed vehicle.

CARLA API: `BlueprintLibrary.find`, `try_spawn_actor`/`spawn_actor`,
`Vehicle.get_physics_control`, `apply_physics_control`, `set_transform`,
`destroy`.

Change Model niszczy starego aktora i tworzy nowego na transformie podniesionym
o 0,5 m. Zachowuje logical name, `scenario_id`, ownership/origin i rolę Ego.
Nowy pojazd używa domyślnej fizyki nowego blueprintu; stara fizyka nie jest
kopiowana. Fizykę odczytuje się ponownie do GUI. Wszystkie sensory przypięte do
starego parenta są zatrzymywane, niszczone i usuwane z rejestru; nie są
automatycznie odtwarzane. Runtime ID zwykle się zmienia.

Zakładki Torque Curve i Steering Curve mają podgląd Canvas aktualizowany po
edycji, dodaniu i usunięciu punktu. Automatyczna siatka 1/2/5, podziałki oraz
etykiety osi dopasowują się do zakresu i rozmiaru widgetu. Torque używa RPM oraz N·m. Steering używa
prędkości w km/h i bezwymiarowego steer scale zgodnie z polem PhysX/Unreal,
do którego CARLA 0.9.15/0.9.16 przekazuje krzywą. Punkty są rysowane według
rosnącej osi X.

Ograniczenia: spawn może zostać odrzucony przez kolizję; CARLA może skorygować
niektóre wartości fizyki; operacja Change Model nie jest transakcją z rollbackiem
po zniszczeniu poprzedniego aktora.

## Actors — Pedestrians

Implementacja: `gui/tabs/pedestrians.py`, `services/actors.py`,
`movement/runtime.py`.

Funkcje odpowiadają wspólnemu lifecycle aktora: blueprint, transform/ghost,
Spawn, Rename, Change Model, Remove, Refresh i Take Ownership. Managed walker
może otworzyć Movement Control.

Po `None` z `try_spawn_actor()` dla `walker.pedestrian.*` wykonywana jest jedna
próba `spawn_actor()` na kopii transformu z Z +2,0 m. Ma to ograniczyć kolizję;
aktor zachowuje fizykę CARLA. Nie ma losowego wyboru punktu ani pętli prób.

Movement Control używa `carla.WalkerControl` dla Manual/fallback oraz
`controller.ai.walker` dla Random/Destination, gdy kontroler działa poprawnie.
Destination ma fallback bezpośredniego sterowania przy stall. Zachowanie na
NavMesh i naturalne opadnięcie po fallbacku wymagają E2E.

## Actors — Static Objects

Implementacja: `gui/tabs/static_objects.py`, `services/actors.py`.

Zakładka obsługuje wybór blueprintu static/prop, ghost preview, spawn,
Rename, transform, Change Model, Remove, Refresh i ownership. Static actors nie
mają Movement Control ani vehicle physics.

Należy odróżnić Managed static object od infrastruktury mapy. Baseline
infrastruktury jest przechowywany dla episode i chroniony przez Clear. External
static objects mają ograniczoną provenance; Configuration Save ich nie obejmuje bez
jawnej adopcji.

## Sensors

Implementacja: `gui/tabs/sensors.py`, `services/sensors.py`,
`gui/windows/sensor_preview.py`, `preview/sensor_buffers.py`.

Funkcje:

- wybór parent actor i blueprintu sensora;
- logical name, atrybuty, relative transform;
- `Attachment Type`: `Rigid` (domyślny), `SpringArm`, `SpringArmGhost`;
- tabela z parentem, ownership, attachment i stanem;
- Rename, transform, preview, stop preview, remove i refresh.

Wybrany attachment jest jawnie mapowany na `carla.AttachmentType`. Brak enumu w
aktywnym API powoduje błąd; nie ma cichego `Rigid`. Typ mocowania istniejącego
sensora jest tylko do odczytu — zmiana wymagałaby respawnu. Starszy scenariusz
bez pola `attachment_type` używa podczas Load legacy default `Rigid`.

Podglądy obejmują formaty image, value, event i spatial zależnie od blueprintu.
RSS jest traktowany jako niedostępny, jeżeli aktywny build API go nie udostępnia;
dokumentacja nie deklaruje pełnej obsługi RSS.

## Simulation Control

Implementacja: `gui/tabs/simulation.py`, `simulation/manager.py`,
`simulation/runtime.py`.

### World Settings

GUI odczytuje, proponuje, waliduje i stosuje `synchronous_mode`,
`fixed_delta_seconds`, `no_rendering_mode`, substepping i jego parametry.
Refresh Settings ponownie odczytuje serwer. Wartości są powiązane z
`World.get_settings()` i `World.apply_settings()`.

### Runtime control

Pause, Resume i Single Step sterują lokalnym schedulerem ticków. Są istotne
przede wszystkim w synchronous mode. Przy wielu klientach aplikacja nie wymusza
globalnie jednego tick mastera; wymaga to uzgodnienia środowiska testowego.

### Configuration Management

Udostępnia Save Configuration JSON/YAML, Load Configuration z preflight/confirm
oraz Clear Managed i Clear Entire World. Zapis dotyczy konfiguracji środowiska,
nie pełnego scenariusza testowego z gwarantowanym przebiegiem. Operacje są
blokowane wzajemnie. Szczegóły i zakres ownership:
[SCENARIO_MANAGEMENT.md](SCENARIO_MANAGEMENT.md).

## Movement Control

Movement Control jest oknem uruchamianym dla jednego Managed vehicle albo
walkera. Zapewnia osobny podgląd Pygame, wybór kamery i celu oraz tryby opisane w
[MOVEMENT_CONTROL.md](MOVEMENT_CONTROL.md).

Dla pojazdu worker co 100 ms odczytuje rzeczywiste `get_velocity()` i
`get_control()`, niezależnie od Manual, Free Roam lub Destination. Ten sam pakiet
aktualizuje Current speed w Tkinter oraz półprzezroczysty HUD Pygame z trybem,
km/h, biegiem, throttle, brake i steering. Klawisz `H` przełącza HUD.

Movement Preview można swobodnie skalować od 480×270. Obraz kamery zachowuje
proporcje przez fit/contain i letterboxing; resize nie zmienia parametrów sensora
CARLA ani nie uruchamia nowego procesu. HUD jest kompozytowany po skalowaniu
obrazu, dlatego zachowuje rozmiar i pozycję w lewym górnym narożniku.

Zamknięcie okna kończy kamerę i manual input session, ale aktywny vehicle
Destination może nadal działać w workerze. Ponowne otwarcie odczytuje runtime
state. Zmiana mapy, usunięcie aktora, Clear, Disconnect i terminalny wynik
nawigacji uruchamiają cleanup odpowiedniego sterowania.

## Zależności między funkcjami

- wszystkie operacje aktora zależą od aktualnego `world_id` i synchronizacji;
- sensor zależy od żywego parenta; Change Model/Remove parenta usuwa sensor;
- Movement wymaga Managed actor i aktualnej pary
  `world_id/world_generation`;
- Configuration Save poprzedza synchronizacja, a Load może zmienić mapę i zamknąć
  preview;
- Dashboard jest projekcją store'a, nie mechanizmem sterowania;
- External World Refresh przebudowuje comboboksy blueprintów, mapy, pogody i
  spawn pointów, aby nie używały cache starego episode.
