# Linux Compatibility Audit

Data audytu: 2026-11-17

## Executive Summary

Statyczna analiza wskazuje, że główna architektura aplikacji jest w dużej
mierze przenośna: ścieżki buduje `pathlib`, operacje Configuration JSON/YAML są
niezależne od separatora systemowego, procesy podglądów jawnie używają metody
`spawn`, a kod produkcyjny nie korzysta z Registry, Win32 API, PowerShell,
`cmd.exe`, `os.startfile` ani platformowego `subprocess`.

Aplikacja została dostosowana kodowo do obu platform: dialogi używają
`tkinter.messagebox`, scroll normalizuje eventy Windows/Wayland/X11, a zapis
cache map trafia do per-user LocalAppData lub XDG Cache. Cele procesów preview
są importowalnymi funkcjami top-level, metoda `spawn` jest jawna, a nieudany
start procesu zwalnia utworzone kolejki i uchwyty.

Ocena gotowości: **implementacja cross-platform przeszła wstępny smoke test
Ubuntu 26.04.1 LTS w WSL2/WSLg z serwerem CARLA 0.9.16 na Windows**. Nie był to
pełny E2E ani test natywnego Linuxa, X11/Wayland na osobnej instalacji lub CARLA
0.9.15.

## Aktualnie wspierane środowisko

- środowisko podstawowe: Windows 11;
- CARLA: 0.9.15 i 0.9.16, z 0.9.16 jako wersją referencyjną;
- zaobserwowane środowiska lokalne: Python 3.10 + CARLA 0.9.15 oraz Python
  3.12 + CARLA 0.9.16;
- WSL2: wstępnie sprawdzone Ubuntu 26.04.1 LTS, Python 3.12.16, CARLA Python
  API 0.9.16 i połączenie z serwerem CARLA 0.9.16 na Windows;
- natywny Linux i Linux/WSL2 z CARLA 0.9.15: nieweryfikowane.

Oficjalne wydania CARLA dostarczają pakiety Windows i Ubuntu:
[0.9.15](https://github.com/carla-simulator/carla/releases/tag/0.9.15) oraz
[0.9.16](https://github.com/carla-simulator/carla/releases/tag/0.9.16).
Fakt dostępności pakietu CARLA nie potwierdza zgodności tego GUI.

## Zależności

| Obszar | Wynik analizy |
|---|---|
| Python | CARLA 0.9.15 publikuje wheel do CPython 3.10; 0.9.16 do CPython 3.12. Dla projektu należy odpowiednio preferować 3.10 i 3.12. |
| CARLA | `../pyproject.toml` udostępnia wzajemnie wykluczające się profile `carla-0-9-15` i `carla-0-9-16`; pip nadal wymaga jawnego wyboru dokładnej wersji. |
| Architektura CPU/libc | Oficjalne wheel Linux są x86-64: 0.9.15 ma tag manylinux glibc 2.27, a 0.9.16 glibc 2.31. ARM64 i starszy glibc nie są pokryte. |
| Tkinter | Jest wymagany przez całe GUI i backend Matplotlib `TkAgg`; na wielu dystrybucjach wymaga osobnego pakietu systemowego, np. `python3-tk`. |
| Pygame | Tworzy osobne okna Movement/Spectator przez SDL. Wymaga aktywnego display i zgodnego backendu X11 lub Wayland. |
| Pillow/NumPy/Matplotlib | Mają przenośne użycie w kodzie; wymagają zgodnych wheel lub toolchainu systemowego. |
| PyYAML | Ładowany dynamicznie przy plikach YAML. JSON nie zależy od niego. |
| Pygments | Importowany przy starcie zakładki Vehicles, więc jest zależnością runtime mimo marginalnego użycia. |
| Dialogi | Wszystkie moduły GUI importują standardowy `tkinter.messagebox`; kod nie wywołuje Zenity ani Xmessage i nie instaluje pakietu PyPI o tej nazwie. |
| Manifest | `../pyproject.toml` zawiera bezpośredni runtime, `../uv.lock` rozwiązanie Windows/Linux, a `../requirements.txt` krótki fallback pip. Jupyter nie jest zależnością runtime. |

Listy plików wheel potwierdzają oficjalne strony
[carla 0.9.15](https://pypi.org/project/carla/0.9.15/) i
[carla 0.9.16](https://pypi.org/project/carla/0.9.16/). Nie instalowano nowych
pakietów podczas audytu.

## Ścieżki i system plików

### Likely portable

- `../core/carla_pythonapi.py` korzysta z `Path`, `expanduser()` i ścieżek
  składanych operatorem `/`; obsługuje `CARLA_PYTHONAPI_PATH` i `CARLA_ROOT`.
- `../scenarios/paths.py` normalizuje rozszerzenia JSON/YAML i buduje ścieżki przez
  `Path`. Sanitizer jest celowo zgodny także z ograniczeniami Windows, więc na
  Linuxie jest jedynie bardziej restrykcyjny niż to konieczne.
- `../scenarios/serialization.py` czyta UTF-8 i zapisuje atomowo przez plik
  tymczasowy w katalogu docelowym, `fsync` i `os.replace`.
- zasoby map są lokalizowane względem `__file__`, a nie bieżącego katalogu.
- nie znaleziono ścieżek z literami dysków ani ręcznego łączenia separatorami w
  produkcyjnym kodzie aplikacji.

### Ryzyka

- `preview/map.py:MapPreviewCache` zapisuje nowe obrazy do LocalAppData na
  Windows albo XDG Cache/`~/.cache` na Linuxie. Dotychczasowe katalogi
  `../resources/maps/generated` i `../images/maps/generated` są nadal odczytywane.
  Gdy testowe lub ograniczone środowisko nie udostępnia katalogu profilu,
  awaryjnie używany jest systemowy katalog tymczasowy.
- Linux rozróżnia wielkość liter. Bieżące ścieżki zasobów mają zgodne nazwy, ale
  pełny przebieg wszystkich map wymaga testu na rzeczywistym filesystemie.
- atomowy `os.replace` jest właściwy dla lokalnego systemu plików; uprawnienia i
  nietypowe udziały sieciowe wymagają osobnej weryfikacji.

## Tkinter, Pygame i sesja graficzna

Główne GUI działa w pętli Tkinter. Sensor Preview osadza Matplotlib przez
`FigureCanvasTkAgg`. Movement Preview i Spectator Preview tworzą osobne okna
Pygame. Aplikacja wymaga zatem aktywnej sesji graficznej; uruchomienie przez SSH
bez display, usługę systemową lub czysty kontener headless nie jest wspierane.

Pygame/SDL może działać natywnie przez X11 lub Wayland, lecz tworzenie dwóch
okien, focus, skróty klawiaturowe, przechwytywanie myszy, resize i cleanup trzeba
sprawdzić osobno na obu stosach. `Segoe UI` z `../gui/styles.py` nie jest typową
czcionką Linux; Tk powinien dobrać zamiennik, ale rozmiary kontrolek mogą się
zmienić.

`../gui/widgets/scrolling.py` normalizuje `<MouseWheel>` oraz X11 `<Button-4>` i
`<Button-5>`. Main View, Add Sensor oraz Navigation Map korzystają z tej samej
interpretacji kierunku. Binding Main View ma identyfikatory i jest zdejmowany,
więc ponowne wejście kursora nie kumuluje handlerów.

## Procesy, wątki i IPC

- `../app.py` ma osłonę `if __name__ == "__main__"` i wywołuje
  `multiprocessing.freeze_support()`.
- `MovementPreviewProcess` i `SpectatorPreviewManager` jawnie pobierają
  `multiprocessing.get_context("spawn")`. Kod nie zależy od linuksowego `fork`.
- IPC wykorzystuje `Event` i ograniczone `Queue`; cele procesów są funkcjami na
  poziomie modułu, co jest zgodne ze `spawn`.
- najpierw żądany jest łagodny stop; awaryjnie następuje `terminate()`, potem
  `kill()` i `join()`. Nie znaleziono własnej obsługi sygnałów POSIX.
- Spectator Preview tworzy własnego klienta CARLA w procesie potomnym; Movement
  Preview otrzymuje klatki i telemetrię przez IPC i nie tworzy drugiego klienta.
- worker CARLA jest oparty na `threading`; aktualizacje Tk są przekazywane do
  pętli GUI.

Układ jest prawdopodobnie przenośny, lecz szybkie zamykanie, reconnect, utrata
serwera oraz brak procesów zombie wymagają kontroli na Linuxie.

## CARLA Python API i Agents

`carla` jest importowana przez modele, usługi, ruch i preview. Musi być binarnie
zgodna z interpreterem oraz wersją serwera. Oficjalny katalog
`PythonAPI/carla/agents` jest potrzebny do `BasicAgent` i
`GlobalRoutePlanner`; aplikacja odnajduje go przez `CARLA_PYTHONAPI_PATH` lub
`CARLA_ROOT` i weryfikuje źródło załadowanego modułu.

Resolver jest przenośny, nie zawiera ścieżek platformowych, a komunikaty
diagnostyczne obejmują CARLA 0.9.15 i 0.9.16.

## Komunikacja klient–serwer

CARLA używa TCP niezależnie od systemu operacyjnego. Następujące układy są
architektonicznie prawdopodobne, ale nie zostały zweryfikowane w tym projekcie:

1. Linux GUI client + Linux CARLA server — wymaga testu natywnego;
2. Linux GUI client + Windows CARLA server — wstępnie sprawdzone w WSL2 dla
   CARLA 0.9.16;
3. Windows GUI client + Linux CARLA server — wymaga testu natywnego.

W każdym układzie wymagane są zgodne wersje API/serwera, osiągalny port RPC
(domyślnie 2000), port strumieniowania (2001) oraz ewentualny port Traffic
Managera (w aplikacji domyślnie 8050). Spectator Preview otwiera drugie
połączenie do tego samego Host/Port. Firewall, NAT, routing i opóźnienie sieci
nie są konfigurowane przez aplikację. Oficjalny quickstart opisuje port RPC i
uruchamianie serwera na obu systemach:
[CARLA quickstart 0.9.15](https://carla.readthedocs.io/en/0.9.15/start_quickstart/).

## Tabela problemów i ryzyk

| Klasyfikacja | Miejsce | Problem i konsekwencja | Ryzyko | Sugerowana poprawka | Architektura / weryfikacja |
|---|---|---|---|---|---|
| Resolved in code | `home.py`, `simulation.py`, `world.py` | Dialogi są oparte na `tkinter.messagebox`; brak zależności wykonawczej od Zenity/Xmessage. | niskie | Manualnie sprawdzić modalność i parent na Linuxie. | Test mock + GUI Linux. |
| Resolved in code | `../gui/widgets/scrolling.py`, Home, Sensors, Navigation Map | Wspólna normalizacja obsługuje Windows/Wayland i X11 bez kumulowania bindingów Main View. | niskie | Sprawdzić fizyczne kółko na X11/Wayland. | Unit/event + GUI Linux. |
| Resolved in dependency config | `../pyproject.toml`, `../uv.lock` | Profile instalują dokładnie jedną wersję CARLA i są konfliktowe; pip zachowuje jawny krok. | niskie | Utrzymywać lock i sprawdzać publikowane wheel obu wersji. | Resolver uv + smoke docelowego systemu. |
| Confirmed environment constraint | wheel CARLA | Python, x86-64 i glibc muszą pasować; brak oficjalnego wheel ARM64 w analizowanych wydaniach. | wysokie | Stosować macierz wersji i kontrolę `carla.__file__`. | Wymaga docelowej dystrybucji Linux. |
| Resolved in code | `preview/map.py:MapPreviewCache` | Nowy cache jest w LocalAppData/XDG; stare zasoby są nadal czytane. | niskie | Sprawdzić uprawnienia docelowego profilu. | Unit mock + smoke Linux. |
| Requires Linux verification | Tk/TkAgg/Pygame | Display, Wayland/X11, focus, resize i wejście mogą różnić się od Windows. | wysokie | Smoke test na X11 i Wayland; nie uruchamiać headless. | Test rzeczywisty. |
| Requires Linux verification | procesy preview | `spawn` jest przenośny, lecz cleanup i okna potomne wymagają testu. | średnie | Test start/close/reconnect i kontrola procesów. | Bez planowanej zmiany. |
| Potential compatibility risk | `../gui/styles.py` | Brak Segoe UI może zmienić geometrię kontrolek. | niskie | Sprawdzić fallback i skalowanie; ewentualnie font systemowy. | Test wizualny Linux. |
| Resolved in code | `../core/carla_pythonapi.py` | Resolver i komunikaty uwzględniają obie wspierane wersje bez sztywnych ścieżek. | niskie | Zweryfikować z dwoma oficjalnymi pakietami. | Unit + smoke Linux. |
| Likely portable | `../scenarios/paths.py`, `serialization.py` | Pathlib, UTF-8 i zapis atomowy nie mają założeń Windows. | niskie | Bez zmian; zweryfikować uprawnienia i YAML. | Unit + smoke Linux. |
| Likely portable | kod systemowy | Brak Win32 API, Registry, PowerShell/cmd i produkcyjnego `subprocess`. | niskie | Bez zmian. | Statycznie potwierdzone. |

## Priorytety ewentualnych poprawek

1. Wykonać smoke test na X11 i Wayland z prawdziwym CARLA.
2. Utrzymywać `pyproject.toml`, `requirements.txt` i `uv.lock` w synchronizacji;
   `carla` nadal dobierać do interpretera i wersji serwera.
3. Po smoke testach dopracować ewentualne różnice fontów i skalowania.

Zmiany kodowe opisane jako Resolved zostały wykonane w etapie kompatybilności;
pozostałe punkty wymagają rzeczywistego środowiska Linux lub osobnego zadania.

## Proponowany plan testów Linux

Najpierw wykonać test na wspieranej dystrybucji x86-64 z aktywnym pulpitem i
lokalnym serwerem tej samej wersji. Następnie powtórzyć na Wayland i X11 oraz z
serwerem zdalnym Windows. Rejestrować wersję dystrybucji, jądra, glibc, Pythona,
CARLA, SDL/Pygame, typ sesji i sterownik GPU.

### Weryfikacja automatyczna implementacji

Na Windows/Python 3.12 wykonano 84 celowane testy bez serwera CARLA. Obejmowały
normalizację scrolla, importy dialogów Tk, ścieżki LocalAppData/XDG, atomowy
cache map, nieudany start `spawn`, geometrię/lifecycle Movement Preview,
importowalność celów procesów oraz Configuration JSON/YAML i regresje ścieżek.
Wynik: **84/84 OK**. Po regresji ujawnionej przez środowisko testowe z całkowicie
wyczyszczonym `os.environ` wykonano dodatkowo Map Preview oraz Traffic Manager
Lifecycle: **37/37 OK**. Testy XDG i eventów X11 są testami funkcji z mockami;
nie stanowią dowodu działania rzeczywistej sesji Linux.

### Minimal Linux Smoke Test Plan

1. Utworzyć świeże venv z Pythonem 3.10 (CARLA 0.9.15) lub zgodnym 3.10–3.12
   (0.9.16); zainstalować requirements i właściwy wheel `carla`.
2. Ustawić `CARLA_PYTHONAPI_PATH`, sprawdzić import `carla` i `BasicAgent`.
3. Uruchomić GUI bez serwera; sprawdzić czytelny błąd połączenia i dialogi.
4. Uruchomić serwer CARLA, połączyć GUI i potwierdzić Dashboard/Connected.
5. Zmienić mapę, poczekać na episode i sprawdzić Refresh World.
6. Wykonać Spawn, edycję i Remove jednego pojazdu oraz pieszego.
7. Utworzyć sensor i sprawdzić Sensor Preview.
8. Otworzyć Movement Control, kolejno Manual/Free Roam/Destination oraz
   Movement Preview; zamknąć okno bez zatrzymania aktywnego trybu autonomicznego.
9. Otworzyć, sterować, resize i zamknąć Spectator Preview.
10. Zapisać konfigurację JSON i YAML, wykonać Preflight/Load/Replace/Clear oraz
    odczytać starszy plik Configuration/Scenario.
11. Wykonać Disconnect/Reconnect oraz zasymulować nieoczekiwaną utratę serwera.
12. Zamknąć GUI i sprawdzić, czy nie pozostały procesy Python/Pygame ani sensory
    Managed; osobno zakończyć serwer.
13. Powtórzyć krytyczne kroki w sesji X11 i Wayland oraz dla serwera zdalnego.

## Ocena gotowości

Kod jest przygotowany do uruchomienia na Windows i Linux: ścieżki zapisywalne,
dialogi, scroll, serializacja oraz `spawn` są przenośne. WSL2/WSLg z Windows
CARLA 0.9.16 ma status **preliminary smoke verified**. Oficjalna deklaracja
zgodności z natywnym Linuxem nadal wymaga planu smoke na X11/Wayland oraz
przebiegów klient–serwer z CARLA 0.9.15 i 0.9.16.
