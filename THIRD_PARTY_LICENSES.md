# Third-Party Licenses

Der eigene Quellcode dieses Projekts steht unter der MIT-Lizenz (siehe `LICENSE`).
Das Projekt verwendet jedoch externe Bibliotheken und Sensor-Software, die unter
eigenen Lizenzen stehen. Diese Lizenzen gelten unabhängig von der MIT-Lizenz
dieses Projekts.

## Verwendete Bibliotheken

| Bibliothek            | Zweck                              | Lizenz        |
|-----------------------|------------------------------------|---------------|
| `adafruit_bme680`     | BME688 (Temp/Feuchte/Druck/Gas)    | MIT           |
| `paho-mqtt`           | MQTT-Client (Pi & Bridge)          | EPL 2.0 / EDL |
| `psycopg2`            | PostgreSQL-Treiber                 | LGPL 3.0      |
| `PyYAML`              | Konfigurationsdateien              | MIT           |
| `FastAPI`             | Web-Backend                        | MIT           |
| `python-jose`         | JWT-Validierung (Cognito)          | MIT           |

Die jeweils aktuell gültigen Lizenztexte liegen bei den Bibliotheken selbst
(via `pip`) bzw. in deren offiziellen Repositories.

## Bosch BSEC (BME688)

Dieses Projekt liest den BME688 über die **Adafruit-Bibliothek** (`adafruit_bme680`,
MIT) aus und verwendet ausschließlich den **Roh-Gaswiderstand** (`gas_resistance`).
Die proprietären **Bosch BSEC**-Algorithmen (Berechnung von IAQ / Air-Quality-Index)
werden **nicht** verwendet und sind **nicht** Teil dieses Repositories.

Wer den BME688 mit BSEC betreiben möchte, muss die Bibliothek separat von Bosch
beziehen. Wichtige Eckpunkte der Bosch-BSEC-Lizenz:

- BSEC ist **proprietär** und wird nur als vorkompiliertes Binary bereitgestellt.
- Der Lizenztext und die Binaries dürfen **nicht** in dieses Repository aufgenommen
  oder unverändert weiterverbreitet werden.
- Die Bosch-Lizenz erlaubt — unter ihren eigenen Bedingungen — auch die
  kommerzielle Nutzung, muss aber bei jeder Weitergabe mit-akzeptiert werden.
- Die jeweils gültige Lizenz und die Software gibt es direkt bei Bosch Sensortec:
  https://www.bosch-sensortec.com/software-tools/software/bsec/

Sollte BSEC zu einem späteren Zeitpunkt integriert werden, ist die Bosch-Lizenz
diesem Repository **zusätzlich** beizulegen (z. B. unter `licenses/BSEC_LICENSE`),
und der BSEC-Binärcode selbst gehört über `.gitignore` ausgeschlossen.
