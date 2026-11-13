# ActivityTracker

ActivityTracker to aplikacja desktopowa Windows, która mierzy czas spędzony w
aplikacjach na podstawie **aktywnego okna**. Zapisuje lokalnie nazwę procesu i
czas używania. Nie zbiera tytułów okien, wpisywanego tekstu, zrzutów ekranu ani
historii przeglądania.

## Wymagania

- Windows 10 lub 11
- Python 3.12 lub nowszy (64-bit)
- Dostęp do pulpitu użytkownika; wykrywanie aktywnego okna i blokady korzysta
  z Windows API

## Instalacja i uruchomienie

W PowerShell, w katalogu projektu:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

Sprawdź `python --version` i upewnij się, że używana wersja to co najmniej
3.12. Testy projektu przechodzą na Pythonie 3.14.

Jeśli PowerShell nie pozwala aktywować środowiska, można pominąć aktywację i
uruchamiać polecenia przez `.\.venv\Scripts\python.exe`.

Po uruchomieniu aplikacja rozpoczyna monitoring w tle. Zamknięcie okna chowa
je do zasobnika systemowego; menu ikony pozwala ponownie otworzyć okno,
wstrzymać/wznowić pomiar, skonfigurować autostart albo zakończyć aplikację.
Autostart jest domyślnie wyłączony.

## Ustawienia i raporty

W sekcji **Ustawienia** można skonfigurować:

- próg bezczynności (domyślnie 5 minut) oraz ignorowane nazwy procesów;
- host, port, tryb TLS, nadawcę, odbiorcę i użytkownika SMTP;
- porę raportu dziennego oraz dzień i porę raportu tygodniowego;
- automatyczne wysyłanie raportów.

Raport dzienny podsumowuje poprzedni lokalny dzień kalendarzowy. Raport
tygodniowy obejmuje ostatni zakończony tydzień od poniedziałku do niedzieli.
Raport jest wysyłany po nadejściu ustawionej pory, gdy aplikacja działa.
Nieudane próby są zapisywane w historii i nie są oznaczane jako wysłane.

Hasło SMTP wpisane w ustawieniach trafia do **Windows Credential Manager**
przez bibliotekę `keyring`; nie jest zapisywane w SQLite ani w pliku
konfiguracyjnym. Pozostawienie pola hasła pustego nie zmienia wpisu w
Credential Managerze. Hasło jest potrzebne, jeśli serwer wymaga
uwierzytelniania; transmisja zawsze wymaga STARTTLS albo SSL.

Przycisk **Wyślij e-mail testowy** wysyła krótką wiadomość na adres odbiorcy
wpisany w formularzu, korzystając z bieżących pól SMTP. Wysłanie odbywa się w
wątku roboczym, więc interfejs pozostaje responsywny. Jeśli wpiszesz hasło,
zostanie ono zapisane w Windows Credential Manager i pole zostanie wyczyszczone;
pozostaw je puste, aby użyć wcześniej zapisanego hasła. Testowa wiadomość nie
zapisuje ani nie modyfikuje ustawień poczty w bazie.

## Dane i prywatność

- Baza SQLite: `%LOCALAPPDATA%\ActivityTracker\activity-tracker.sqlite3`
- Log aplikacji: `%LOCALAPPDATA%\ActivityTracker\activity-tracker.log`
- Do pomiaru używana jest nazwa procesu aktywnego okna, nie lista procesów
  działających w tle.
- Czas nie jest naliczany przy zablokowanym komputerze, bezczynności,
  wstrzymanym monitorowaniu ani braku kwalifikującego się aktywnego okna.
- Dane pozostają lokalne, z wyjątkiem raportu, który użytkownik włączy i
  skonfiguruje do wysłania na wskazany adres.

## Testy

Zainstaluj zależności deweloperskie i uruchom testy w PowerShell:

```powershell
python -m pip install -r requirements-dev.txt
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

Testy logiki używają atrap zegara, Windows API i SMTP. Pełne działanie
monitorowania, zasobnika, Windows Credential Manager oraz autostartu należy
sprawdzić na koncie Windows z aktywnym pulpitem.

## Budowanie EXE

Na Windows, w aktywnym środowisku wirtualnym, zainstaluj zależności
deweloperskie i zbuduj aplikację:

```powershell
python -m pip install -r requirements-dev.txt
python -m PyInstaller --clean --noconfirm ActivityTracker.spec
```

Gotowy plik one-file znajduje się w `dist\ActivityTracker.exe`. Spec dołącza
szablony HTML raportów oraz backend Windows Credential Manager. Pierwsze
uruchomienie może potrwać dłużej, ponieważ PyInstaller rozpakowuje zależności.
EXE należy budować i testować na Windows; PyInstaller nie jest kompilatorem
krzyżowym.

## Architektura

| Moduł | Odpowiedzialność |
| --- | --- |
| `main.py` | Inicjalizacja usług i cykl życia aplikacji |
| `config/settings.py` | Walidacja i trwały zapis ustawień |
| `core/database.py` | SQLite: sesje, ustawienia, agregaty i historia raportów |
| `core/process_manager.py` | Identyfikacja procesu aktywnego okna |
| `core/idle_detector.py` | Wykrywanie bezczynności i blokady Windows |
| `core/monitor.py` | Jednosekundowe próbkowanie i naliczanie czasu |
| `core/scheduler.py` | Harmonogram raportów dziennych i tygodniowych |
| `core/autostart.py` | Autostart użytkownika przez folder Startup |
| `gui/` | Dashboard, statystyki, ustawienia, tray |
| `reports/` | Generowanie HTML/PNG i bezpieczna wysyłka SMTP |
| `tests/` | Testy bazy, monitorowania, GUI, ustawień i raportów |

Monitor oraz wysyłka raportów działają poza wątkiem interfejsu. Czas sesji jest
dzielony na lokalnych granicach dnia, a po nieoczekiwanym wyłączeniu otwarta
sesja jest odzyskiwana do ostatniego zapisanego checkpointu.
