# Workflow projektu CARLA Simulator Manager

Dokument opisuje sposób rozwijania i weryfikowania projektu po zakończeniu reorganizacji architektury 4B.10. Aktualny kod jest nadrzędnym źródłem informacji, a szczegółowy opis struktury znajduje się w [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md).

## 1. Uruchamianie aplikacji

Jedynym oficjalnym punktem wejścia jest:

```powershell
python app.py
```

Przed uruchomieniem GUI należy uruchomić serwer CARLA i przygotować środowisko zgodnie z [`INSTALLATION.md`](INSTALLATION.md). Import modułu `app` nie powinien tworzyć okna Tkinter, klienta CARLA, procesu ani wątku.

## 2. Organizacja kodu

Nowy kod powinien trafiać do pakietu odpowiadającego jego odpowiedzialności:

| Pakiet | Odpowiedzialność |
|---|---|
| `core/` | kontroler, kolejki, lifecycle, kontrakty i współdzielony stan |
| `models/` | modele danych niezależne od GUI |
| `services/` | operacje CARLA dotyczące świata, aktorów, sensorów i blueprintów |
| `gui/` | aplikacja Tkinter, zakładki, okna i współdzielone widgety |
| `movement/` | sterowanie pojazdami i pieszymi oraz sesje Movement Control |
| `simulation/` | tryby symulacji, tick scheduler i dashboard |
| `scenarios/` | zapis, odczyt, walidacja, synchronizacja i ownership |
| `preview/` | procesy podglądów, bufory sensorów i generator map |

Nie należy przywracać starych modułów zgodności, takich jak `carla_manager.py`, `carla_server/`, `gui_contents/` czy `scenario_management/`. Importy powinny korzystać wyłącznie z aktualnych pakietów.

## 3. Granice architektoniczne

- GUI przekazuje operacje do `CarlaController` przez jego publiczne API i istniejące kolejki.
- Operacje mutujące świat CARLA wykonuje worker kontrolera. Widoki Tkinter nie powinny wykonywać nowych bezpośrednich wywołań CARLA RPC.
- `services/` i moduły runtime nie importują konkretnych widoków ani klasy `Manager`.
- `models/` nie importuje GUI, kontrolera ani usług.
- W ramach jednego `Manager` istnieje jedna współdzielona instancja `DataStorage` i jeden zestaw rejestrów aktorów oraz sensorów.
- Należy zachować rozróżnienie aktorów `MANAGED` i `EXTERNAL`.
- Callbacki sensorów zapisują dane do thread-safe buforów; nie aktualizują bezpośrednio widgetów Tkinter.
- Procesy Movement Preview i Spectator Preview zachowują importowalne targety najwyższego poziomu, wymagane przez Windows `multiprocessing.spawn`.
- `multiprocessing.freeze_support()` pozostaje wywoływane w `app.py`.

## 4. Przebieg zmiany

1. Określ obszar funkcjonalny oraz moduły, których dotyczy zmiana.
2. Sprawdź aktualny kod i odpowiadającą mu dokumentację architektury.
3. Wprowadź możliwie małą zmianę bez modyfikowania niezwiązanych komponentów.
4. Zachowaj publiczne formaty komend, zdarzeń i scenariuszy, o ile zadanie jawnie nie wymaga ich zmiany.
5. Dodaj lub zaktualizuj testy obserwowalnego zachowania.
6. Najpierw uruchom test pojedynczego modułu lub obszaru.
7. Przed zakończeniem większej zmiany wykonaj jeden pełny przebieg regresji.
8. Zaktualizuj dokumentację opisującą zmieniony stan projektu.

## 5. Testy automatyczne

Projekt używa `unittest`. Testy należy uruchamiać z katalogu głównego repozytorium, używając interpretera skonfigurowanego dla projektu.

Cały zestaw:

```powershell
python -m unittest discover -s tests -t .
```

Wybrany obszar, przykładowo Movement Control:

```powershell
python -m unittest discover -s tests/movement -t .
```

Pojedynczy moduł:

```powershell
python -m unittest tests.movement.test_vehicle_control
```

Testy jednostkowe nie powinny uruchamiać serwera CARLA, prawdziwych okien Tkinter/Pygame ani wymagać GPU. Zależności CARLA zastępuje się fake'ami i mockami, patchując symbol w miejscu jego użycia.

Aktualny zakres i znane luki opisują:

- [`docs/testing/UNIT_TESTS.md`](docs/testing/UNIT_TESTS.md),
- [`docs/testing/TEST_COVERAGE.md`](docs/testing/TEST_COVERAGE.md).

## 6. Weryfikacja manualna

Zmiany dotyczące integracji z symulatorem wymagają, zależnie od zakresu, sprawdzenia:

- Connect, Disconnect i Reconnect;
- zmiany świata oraz pogody;
- tworzenia, edycji i usuwania aktorów;
- synchronizacji aktorów i Take Ownership;
- sensorów oraz okien Sensor Preview;
- Movement Control, autopilota i destination;
- trybów sync/async, ticków, pause/resume i dashboardu;
- Save, Load i Clear scenariuszy;
- Movement Preview i Spectator Preview;
- cleanupu przy zmianie mapy, rozłączeniu i zamknięciu aplikacji.

Testów manualnych nie należy oznaczać jako wykonane, jeżeli nie użyto rzeczywistego serwera CARLA i środowiska graficznego.

## 7. Dokumentacja

Po zmianie architektury lub zachowania należy zaktualizować odpowiednie dokumenty:

- `README.md` — ogólny opis i uruchamianie;
- `INSTALLATION.md` — środowisko i konfiguracja;
- `docs/PROJECT_STRUCTURE.md` — organizacja plików;
- `docs/architecture/` — architektura, komunikacja i przepływ danych;
- `docs/testing/` — organizacja oraz zakres testów.

Raporty implementacyjne w `docs/implementation/` są lokalną historią projektu i nie powinny być źródłem prawdy o aktualnym kodzie.

## 8. Zasoby i dane użytkownika

- Dostarczane mapy znajdują się w `resources/maps/bundled/`.
- Generowany cache trafia do `resources/maps/generated/`.
- Starszy cache `images/maps/generated/` może być nadal odczytywany dla zgodności danych.
- Nie należy usuwać ani automatycznie przenosić scenariuszy, cache ani innych danych użytkownika.

## 9. Git i zakres zmian

- Nie commituj wygenerowanego cache, plików `__pycache__` ani lokalnych raportów z `docs/implementation/`.
- Nie wykonuj commitów ani push bez wyraźnego polecenia.
- Nie cofaj niezwiązanych zmian znajdujących się w katalogu roboczym.
- Przed przekazaniem zmiany sprawdź `git status` i opisz testy, których nie można było wykonać.
