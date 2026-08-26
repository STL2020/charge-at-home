"""
FTP-Service — wieder eingebaut auf ausdruecklichen Wunsch des Auftraggebers.

Zweck jetzt bewusst anders als beim ersten Mal: nicht mehr nur zum Importieren
einer bekannten Logger-Datei, sondern zum echten, freien DURCHSUCHEN des
Miniserver-Dateisystems — der Auftraggeber vermutet zu Recht, dass die in der
Loxone-App sichtbare Historie irgendwo als echte Datei auf dem Miniserver
liegen muss, auch ohne dass wir vorher wissen, wo genau.

MLSD (moderneres, strukturiertes Listing) wird zuerst versucht; bei JEDEM
Fehler (nicht nur Permission-Fehlern — frueherer Bug, siehe Pflichtenheft-
Changelog) faellt der Code auf das einfachere NLST zurueck.
"""

import ftplib
import io


def list_directory(host: str, username: str, password: str, path: str = "/") -> tuple[list[dict] | None, str]:
    """Listet eine FTP-Verzeichnis. Rueckgabe: (Liste von {name, is_dir, size}, Fehlermeldung)."""
    try:
        ftp = ftplib.FTP(host, timeout=8)
        ftp.login(username, password)
    except Exception as exc:
        return None, f"FTP-Verbindung fehlgeschlagen: {exc}"

    entries = []
    try:
        try:
            ftp.cwd(path)
        except ftplib.all_errors as exc:
            return None, f"Verzeichnis nicht erreichbar: {exc}"

        try:
            # Modernes, strukturiertes Listing (liefert Typ + Groesse direkt mit)
            for name, facts in ftp.mlsd():
                if name in (".", ".."):
                    continue
                is_dir = facts.get("type") == "dir"
                size = int(facts.get("size", 0)) if not is_dir and facts.get("size") else 0
                entries.append({"name": name, "is_dir": is_dir, "size": size})
        except ftplib.all_errors:
            # Fallback: einfaches NLST, Groesse/Typ einzeln nachfragen wo moeglich.
            names = ftp.nlst()
            for name in names:
                if name in (".", ".."):
                    continue
                is_dir = False
                size = 0
                try:
                    size = ftp.size(name)
                    if size is None:
                        is_dir = True
                        size = 0
                except ftplib.all_errors:
                    is_dir = True
                entries.append({"name": name, "is_dir": is_dir, "size": size or 0})
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            pass

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries, ""


def download_file(host: str, username: str, password: str, path: str) -> tuple[bytes | None, str]:
    """Laedt eine einzelne Datei komplett herunter. Rueckgabe: (Inhalt als bytes, Fehlermeldung)."""
    try:
        ftp = ftplib.FTP(host, timeout=8)
        ftp.login(username, password)
    except Exception as exc:
        return None, f"FTP-Verbindung fehlgeschlagen: {exc}"

    buf = io.BytesIO()
    try:
        ftp.retrbinary(f"RETR {path}", buf.write)
    except ftplib.all_errors as exc:
        return None, f"Datei konnte nicht geladen werden: {exc}"
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            pass

    return buf.getvalue(), ""
