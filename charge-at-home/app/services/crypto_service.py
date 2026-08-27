"""
Speicher-Service fuer Loxone-Zugangsdaten.

ENTSCHEIDUNG (siehe Pflichtenheft-Changelog): Auf ausdruecklichen Wunsch des
Auftraggebers wurde die Verschluesselung (Fernet/AES) ENTFERNT und durch
reine Klartext-Speicherung in der Datenbank ersetzt. Begruendung des
Auftraggebers: Es handelt sich um Zugangsdaten fuer den eigenen Miniserver im
eigenen Heimnetz (keine Bankdaten, kein kritisches Geheimnis), waehrend die
Verschluesselungs-Schluessel-Verwaltung wiederholt zu echten, fuer den Nutzer
sehr stoerenden Bugs gefuehrt hat (Schluessel-Verlust nach Updates,
vermutete Race-Condition zwischen den drei parallelen Prozessen beim
allerersten Schluessel-Erzeugen). Die Abwaegung Sicherheit vs. Zuverlaessigkeit
faellt hier bewusst zugunsten der Zuverlaessigkeit aus.

Die Funktionsnamen (encrypt/decrypt) bleiben unveraendert, damit an keiner
der zahlreichen Aufrufstellen im uebrigen Code etwas geaendert werden muss —
sie sind jetzt aber reine Durchreich-Funktionen ohne kryptografische Wirkung.

WICHTIG fuer bereits gespeicherte Wallboxen: Ein zuvor mit Fernet
verschluesseltes Passwort wird durch diesen Wechsel NICHT automatisch les-
bar (der alte Geheimtext ergibt keinen Sinn, wenn er einfach als Klartext
zurueckgegeben wird). Betroffene Wallboxen muessen ihr Passwort EINMALIG neu
eingeben und speichern — ab dann ist es dauerhaft nutzbar, ohne jemals
wieder von einem Schluessel abzuhaengen.
"""


def encrypt(plaintext: str) -> str:
    return plaintext


def decrypt(ciphertext: str) -> str:
    return ciphertext
