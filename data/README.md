# Persistente data

Deze map bevat runtime-data van het Hornet Tracker dashboard.

## Bestanden

- `coord-log.json` — gelogde coördinaten met status, notitie, source.
  Gegenereerd door de Flask backend bij elke log-actie. Staat in
  `.gitignore` omdat het per-installatie data is, geen broncode.

## Backup

Wil je je log bewaren tussen Pi-reflashes: kopieer `coord-log.json`
naar een veilige plek voor je de SD-kaart wist.
