# Familien-Service

Kleine Buchungs-App für Taschengeld-Dienste (Flask + Cloud-Postgres), für iPhone/iPad als Homescreen-App gedacht.

## Seiten
- **Startseite** mit Spruch, Benutzersymbol rechts oben (Anmelden / Registrieren)
- Nach dem Login **Burger-Menü** links oben. Familienmitglieder sehen: Startseite, Pakete & Services (Shop mit Kategorie-Dropdown), Bestellungen, Abrechnungen, Mein Konto
- **Admin-Konto = reine Verwaltung** (z. B. Leo): Aufträge abhaken, Familie und Zahlungen, Pakete und Kategorien verwalten, Mein Konto (PIN ändern). Der Admin hat keine eigenen Bestellungen und kann nichts buchen. Die Kundenseiten sind für ihn auch serverseitig gesperrt.

## Bausteine
- **Datenbank:** ein Postgres in der Cloud (z. B. Supabase, Neon). Nur die Verbindungs-URL wird gebraucht, die Tabellen legt die App beim ersten Start selbst an.
- **App:** läuft als Docker-Container bei einem Hoster (z. B. Render, Fly.io, Railway).

## Umgebungsvariablen
| Name | Bedeutung |
|---|---|
| `DATABASE_URL` | Postgres-Verbindungs-URL (`postgresql://...`) |
| `SECRET_KEY` | Zufälliger Text, mind. 16 Zeichen. **Nie ändern**, sonst passen keine PINs mehr |
| `ADMIN_NAME`, `ADMIN_PIN` | Admin-Zugang (Bruder), nur beim allerersten Start relevant |
| `BUSINESS_NAME` | Name oben in der App |
| `TRUST_PROXY` | `1` beim Hoster (HTTPS, echte Client-IP, sichere Cookies) |
| `PORT` | vom Hoster gesetzt, Standard 8080 |

## Lokal ausprobieren
```
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@host/db SECRET_KEY=irgendein-langer-text ADMIN_PIN=123456
python app.py
```

## Auf dem iPhone installieren
Link in **Safari** öffnen, Teilen-Symbol, **Zum Home-Bildschirm**. Beim ersten Start in der Homescreen-App einmal mit der PIN anmelden.

## Supabase
- `DATABASE_URL`: im Dashboard **Connect**, **Session pooler**, URI kopieren, `[YOUR-PASSWORD]` ersetzen (Passwort ohne Sonderzeichen wählen).
- Die App aktiviert Row Level Security auf allen Tabellen, damit die Supabase-REST-API keinen Zugriff hat.
- Free-Projekte werden bei zu wenig Aktivität pausiert und haben keine herunterladbaren Backups.

## Backup
Alles liegt in der Postgres-Datenbank. Bei Free-Plänen selbst sichern, z. B. gelegentlich `pg_dump "$DATABASE_URL" > backup.sql`.
