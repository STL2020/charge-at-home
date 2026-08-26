#!/usr/bin/perl

# eCharge@Home OCPP — Weboberfläche des Plugins
#
# Vier Reiter: Übersicht, Einstellungen, Diagnose, Hilfe.
#
# Das Protokoll steht unter Diagnose. Es wird aus zwei Quellen gespeist:
# der Datenbank (letzte Meldungen, über /api/status) und der Logdatei
# (start.log, für den Fall dass der Dienst gar nicht erst hochkommt).

use CGI;
use LoxBerry::System;
use LoxBerry::Web;
use LoxBerry::Log;
use warnings;
use strict;

my $cgi     = CGI->new;
my $q       = $cgi->Vars;
my $version = LoxBerry::System::pluginversion();

my $log = LoxBerry::Log->new(name => 'plugin', addtime => 1);
$log->LOGSTART("index.cgi");

# Pfade
my $heim   = $ENV{LBHOMEDIR} || '/opt/loxberry';
my $ordner = $0;
$ordner =~ s{.*/plugins/([^/]+)/[^/]+$}{$1};
$ordner = 'echargeocpp' if !$ordner || $ordner eq $0;

my $datadir = "$heim/data/plugins/$ordner";
my $logdir  = "$heim/log/plugins/$ordner";
my $konfig  = "$datadir/konfig.json";
my $portdat = "$datadir/ports.json";

my $skript;
for my $k ("$heim/bin/plugins/$ordner/dienst.sh",
           "$heim/bin/plugins/echargeocpp/dienst.sh") {
    if (-x $k) { $skript = $k; last; }
}

# ── Konfiguration lesen ───────────────────────────────────────────────────
sub kfg_lesen {
    my %w = (ocpp_port => 9000, http_port => 8042, price_per_kwh => '0.28',
             mqtt_aktiv => 0, mqtt_host => '', mqtt_port => 1883,
             mqtt_user => '', mqtt_pass => '', mqtt_topic => 'echargeocpp');
    if (-r $konfig && open(my $fh, '<', $konfig)) {
        local $/; my $t = <$fh>; close $fh;
        $w{ocpp_port}     = $1 if $t =~ /"ocpp_port"\s*:\s*(\d+)/;
        $w{http_port}     = $1 if $t =~ /"http_port"\s*:\s*(\d+)/;
        $w{price_per_kwh} = $1 if $t =~ /"price_per_kwh"\s*:\s*([\d.]+)/;
        $w{mqtt_aktiv}    = ($t =~ /"mqtt_aktiv"\s*:\s*true/) ? 1 : 0;
        $w{mqtt_host}     = $1 if $t =~ /"mqtt_host"\s*:\s*"([^"]*)"/;
        $w{mqtt_port}     = $1 if $t =~ /"mqtt_port"\s*:\s*(\d+)/;
        $w{mqtt_user}     = $1 if $t =~ /"mqtt_user"\s*:\s*"([^"]*)"/;
        $w{mqtt_pass}     = $1 if $t =~ /"mqtt_pass"\s*:\s*"([^"]*)"/;
        $w{mqtt_topic}    = $1 if $t =~ /"mqtt_topic"\s*:\s*"([^"]*)"/;
    }
    return %w;
}

sub ports_lesen {
    my %p = (ocpp => 9000, http => 8042);
    if (-r $portdat && open(my $fh, '<', $portdat)) {
        local $/; my $t = <$fh>; close $fh;
        $p{ocpp} = $1 if $t =~ /"ocpp"\s*:\s*(\d+)/;
        $p{http} = $1 if $t =~ /"http"\s*:\s*(\d+)/;
    }
    return %p;
}

my %kfg = kfg_lesen();
my %lp  = ports_lesen();

# Läuft der Dienst? Für die Farbe der Schaltflächen.
my $laeuft = 0;
if ($skript) {
    system("$skript status >/dev/null 2>&1");
    $laeuft = ($? == 0) ? 1 : 0;
}

# ── Aktionen ──────────────────────────────────────────────────────────────
my ($meldung, $art) = ('', '');

if (($q->{aktion} || '') =~ /^(start|stop|restart)$/) {
    my $a = $1;
    LOGINF("Dienst-Aktion: $a");
    if ($skript) {
        my $aus = `$skript $a 2>&1`;
        $aus =~ s/[<>]//g;
        $meldung = $aus;
        $art = ($aus =~ /gestartet|beendet/) ? 'ok' : 'warn';
    } else {
        $meldung = 'dienst.sh nicht gefunden — Plugin neu installieren.';
        $art = 'err';
    }
    %lp = ports_lesen();
    if ($skript) { system("$skript status >/dev/null 2>&1"); $laeuft = ($? == 0) ? 1 : 0; }
}

if (($q->{aktion} || '') eq 'speichern') {
    LOGINF("Einstellungen speichern");
    my $op = $q->{ocpp_port} || 9000;
    my $hp = $q->{http_port} || 8042;
    my $pr = $q->{price_per_kwh} || '0.28';
    $op =~ s/\D//g; $hp =~ s/\D//g; $pr =~ s/[^\d.]//g;

    if ($op < 1024 || $op > 65535 || $hp < 1024 || $hp > 65535) {
        $meldung = 'Ports müssen zwischen 1024 und 65535 liegen.';
        $art = 'warn';
    } elsif ($op == $hp) {
        $meldung = 'OCPP-Port und Schnittstellen-Port müssen verschieden sein.';
        $art = 'warn';
    } else {
        mkdir $datadir unless -d $datadir;
        my $ma = $q->{mqtt_aktiv} ? 'true' : 'false';
        my $mh = $q->{mqtt_host}  // '';
        my $mp = $q->{mqtt_port}  || 1883;
        my $mu = $q->{mqtt_user}  // '';
        my $mw = $q->{mqtt_pass}  // '';
        my $mt = $q->{mqtt_topic} || 'echargeocpp';
        $mp =~ s/\D//g; $mp ||= 1883;
        for ($mh, $mu, $mw, $mt) { s/"/\\"/g }

        if (open(my $fh, '>', $konfig)) {
            print $fh qq({\n  "ocpp_port": $op,\n  "http_port": $hp,\n)
                    . qq(  "price_per_kwh": $pr,\n  "mqtt_aktiv": $ma,\n)
                    . qq(  "mqtt_host": "$mh",\n  "mqtt_port": $mp,\n)
                    . qq(  "mqtt_user": "$mu",\n  "mqtt_pass": "$mw",\n)
                    . qq(  "mqtt_topic": "$mt"\n}\n);
            close $fh;
            %kfg = kfg_lesen();
            my $aus = $skript ? `$skript restart 2>&1` : 'dienst.sh fehlt';
            $aus =~ s/[<>]//g;
            %lp = ports_lesen();
            $meldung = "Gespeichert. OCPP-Port $op, Schnittstelle $hp.\n$aus";
            $art = ($aus =~ /gestartet/) ? 'ok' : 'warn';
            if ($skript) { system("$skript status >/dev/null 2>&1"); $laeuft = ($? == 0) ? 1 : 0; }
        } else {
            $meldung = "Datei $konfig nicht schreibbar. Rechte prüfen.";
            $art = 'err';
        }
    }
}

# ── Startprotokoll lesen ──────────────────────────────────────────────────
# Als Array von Zeilen, damit JavaScript sie nicht erst zerlegen muss.
my @loglines;
for my $datei ("$logdir/ocpp.log", "$logdir/start.log") {
    next unless -r $datei;
    if (open(my $fh, '<', $datei)) {
        my @z = <$fh>;
        close $fh;
        @z = splice(@z, -30) if @z > 30;
        push @loglines, @z;
    }
}
# Jede Zeile für JavaScript aufbereiten: escapen, als JSON-Array
my $log_json = '[';
for my $z (@loglines) {
    chomp $z;
    next unless $z =~ /\S/;
    $z =~ s/\\/\\\\/g;
    $z =~ s/"/\\"/g;
    $z =~ s/[<>]//g;
    $log_json .= qq{"$z",};
}
$log_json =~ s/,$//;
$log_json .= ']';

my $titel = "eCharge\@Home OCPP v$version";
LoxBerry::Web::lbheader($titel, "https://www.loewemann.com/", "help.html", "nojqm");
print LoxBerry::Log::get_notifications_html($lbpplugindir);

# Die technische Rückmeldung ("Dienst gestartet, PID …") gehört nicht über
# den Kopf der Seite — sie steht unter Diagnose. Hier bleibt nur eine kurze
# Bestätigung, damit der Anwender sieht, dass sein Klick angekommen ist.
my $kurzmeldung = '';
if ($meldung) {
    if    ($meldung =~ /Dienst gestartet/) { $kurzmeldung = 'Dienst wurde gestartet.'; }
    elsif ($meldung =~ /Dienst beendet/)   { $kurzmeldung = 'Dienst wurde angehalten.'; }
    elsif ($meldung =~ /^Gespeichert/)     { $kurzmeldung = 'Einstellungen gespeichert, Dienst neu gestartet.'; }
    elsif ($art eq 'ok')                   { $kurzmeldung = 'Erledigt.'; }
    else                                   { $kurzmeldung = $meldung; }
}

my $lauf_txt   = $laeuft ? 'läuft' : 'gestoppt';
my $lauf_farbe = $laeuft ? '#1a9c4a' : '#c0392b';
my $start_dis  = $laeuft ? 'disabled' : '';
my $start_cls  = $laeuft ? 'eco-btn eco-btn-aus' : 'eco-btn eco-btn-start';

my $html = <<'HTML';
<style>
/* Kopfbereich */
.eco-kopf { display:flex; align-items:flex-end; gap:20px; margin-bottom:22px;
    padding-bottom:16px; border-bottom:1px solid #e0e4e8; flex-wrap:wrap; }
.eco-kopf-logo { flex:none; width:52px; height:52px; align-self:center; }
.eco-kopf-text h1 { margin:0; font-size:28px; font-weight:700; color:#1a232d;
    letter-spacing:-.02em; line-height:1.15; }
.eco-kopf-text p { margin:6px 0 0; font-size:13.5px; color:#8896a3; line-height:1.5; }
.eco-kopf-status { margin-left:auto; text-align:right; }
.eco-kopf-status .zeile { font-size:13px; font-weight:600; }
.eco-kopf-status .sub { font-size:11.5px; color:#8896a3; margin-top:2px; }
.eco-tabs { display:flex; border-bottom:2px solid #e0e4e8; margin-bottom:20px; }
.eco-tab { padding:10px 18px; font-size:13.5px; color:#5c6b7a; cursor:pointer;
    border:none; background:none; border-bottom:2px solid transparent;
    margin-bottom:-2px; font-family:inherit; transition:color .15s; }
.eco-tab:hover { color:#1a232d; }
.eco-tab.on { color:#1a9c4a; border-bottom-color:#1a9c4a; font-weight:700; }
.eco-pane { display:none; }
.eco-pane.on { display:block; }

.eco-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
    gap:12px; margin-bottom:18px; }
.eco-tile { border:1px solid #dde2e8; border-radius:8px; padding:14px 16px; background:#fff; }
.eco-tile .l { font-size:10px; text-transform:uppercase; letter-spacing:.07em;
    color:#8896a3; font-weight:700; }
.eco-tile .v { font-size:22px; font-weight:700; margin-top:4px; color:#1a232d;
    font-family:"SF Mono",Menlo,monospace; }
.eco-tile .s { font-size:11.5px; color:#8896a3; margin-top:3px; }

.eco-btn { display:inline-block; border:1px solid #d0d8e0; background:#fff; color:#2c3e50;
    padding:9px 18px; border-radius:6px; font-size:13px; font-weight:500; cursor:pointer;
    font-family:inherit; transition:all .15s; }
.eco-btn:hover { border-color:#1a9c4a; color:#1a9c4a; background:#f0f8f3; }
.eco-btn-start { background:#1a9c4a; border-color:#1a9c4a; color:#fff; font-weight:600; }
.eco-btn-start:hover { background:#158a41; color:#fff; }
.eco-btn-aus { background:#eef0f2; border-color:#dde2e8; color:#a0aab4; cursor:default; }
.eco-btn-aus:hover { background:#eef0f2; border-color:#dde2e8; color:#a0aab4; }
.eco-btn-stop { color:#c0392b; }
.eco-btn-stop:hover { border-color:#c0392b; background:#fdf0f0; color:#c0392b; }

.eco-conn { background:#fff; border:1px solid #dde2e8; border-radius:8px;
    display:grid; grid-template-columns:1fr 1fr; overflow:hidden; margin-bottom:18px; }
.eco-conn > div { padding:14px 18px; border-bottom:1px solid #edf0f3; }
.eco-conn > div:nth-child(odd) { border-right:1px solid #edf0f3; }
.eco-conn > div:nth-last-child(-n+2) { border-bottom:none; }
.eco-conn .l { font-size:10.5px; text-transform:uppercase; letter-spacing:.06em;
    color:#8896a3; font-weight:700; margin-bottom:6px; }
.eco-conn .a { font-family:"SF Mono",Menlo,Consolas,monospace; font-size:14px;
    font-weight:600; color:#1a9c4a; word-break:break-all; }
.eco-badge { display:inline-block; padding:4px 11px; border-radius:20px;
    font-size:12px; font-weight:600; }
.b-ok { background:#d4f0de; color:#146b35; }
.b-wn { background:#fef3cd; color:#7d5a00; }
.b-off { background:#e8ebee; color:#5c6b7a; }

.eco-promo {
  display:flex; align-items:center; gap:18px;
  background:linear-gradient(135deg,#0f1923 0%,#1a2d3d 100%);
  border:1px solid #2a3f52; border-radius:8px;
  padding:16px 20px; margin-bottom:18px; text-decoration:none;
  transition:border-color .2s;
}
.eco-promo:hover { border-color:#e8a33d; }
.eco-promo-logo { flex:0 0 44px; height:44px;
  background:#e8a33d; border-radius:8px;
  display:flex; align-items:center; justify-content:center; }
.eco-promo-logo svg { width:26px; height:26px; }
.eco-promo-text { flex:1; min-width:0; }
.eco-promo-text strong { display:block; font-size:14px;
  color:#e8a33d; letter-spacing:.02em; }
.eco-promo-text span { font-size:12px; color:#8aa0b3; line-height:1.5; }
.eco-promo-arrow { flex:0 0 auto; color:#e8a33d; font-size:20px; opacity:.7; }

/* Scrollender Infoband — zeigt was die Hauptanwendung kann */
.eco-ticker-wrap { overflow:hidden; background:#f7f9fb;
  border:1px solid #dde2e8; border-radius:6px;
  padding:8px 0; margin-bottom:18px; }
.eco-ticker { display:inline-block; white-space:nowrap;
  animation:ticker 28s linear infinite; font-size:12px; color:#4a5568; }
.eco-ticker:hover { animation-play-state:paused; }
@keyframes ticker {
  0%   { transform:translateX(100vw); }
  100% { transform:translateX(-100%); }
}
.eco-ticker b { color:#e8a33d; }

.eco-card { background:#fff; border:1px solid #dde2e8; border-radius:8px;
    padding:18px 20px; margin-bottom:16px; }
.eco-card h3 { margin:0 0 4px; font-size:14.5px; color:#1a232d; }
.eco-card .u { font-size:12px; color:#8896a3; margin-bottom:14px; line-height:1.55; }

.eco-f { margin-bottom:14px; max-width:340px; }
.eco-f label { display:block; font-size:12px; font-weight:600; color:#2c3e50; margin-bottom:4px; }
.eco-f input[type=text], .eco-f input[type=number], .eco-f input[type=password] {
    width:100%; padding:8px 11px; border:1px solid #c8d0d8; border-radius:6px;
    font-size:13px; font-family:inherit; box-sizing:border-box; }
.eco-f input:focus { outline:none; border-color:#1a9c4a; box-shadow:0 0 0 3px rgba(26,156,74,.12); }
.eco-f .h { font-size:11.5px; color:#8896a3; margin-top:4px; line-height:1.5; }
.eco-schalter { display:flex; align-items:flex-start; gap:12px; cursor:pointer;
    border:2px solid #dde2e8; border-radius:8px; padding:14px 16px; margin-bottom:16px;
    max-width:460px; transition:all .15s; background:#fff; }
.eco-schalter:hover { border-color:#1a9c4a; background:#f7fcf9; }
.eco-schalter input { position:absolute; opacity:0; width:0; height:0; }
.eco-schalter-box { flex:none; width:44px; height:24px; border-radius:12px;
    background:#c8d0d8; position:relative; transition:background .2s; margin-top:2px; }
.eco-schalter-box::after { content:""; position:absolute; top:3px; left:3px;
    width:18px; height:18px; border-radius:50%; background:#fff;
    transition:transform .2s; box-shadow:0 1px 3px rgba(0,0,0,.3); }
.eco-schalter input:checked + .eco-schalter-box { background:#1a9c4a; }
.eco-schalter input:checked + .eco-schalter-box::after { transform:translateX(20px); }

.eco-step { display:flex; gap:12px; margin-bottom:14px; }
.eco-step .n { flex:none; width:22px; height:22px; border-radius:50%; background:#1a9c4a;
    color:#fff; font-size:11px; font-weight:700; display:flex; align-items:center;
    justify-content:center; }
.eco-step .t { font-size:13.5px; line-height:1.65; color:#3a4a5a; }
.eco-adr { font-family:"SF Mono",Menlo,monospace; font-size:14px; font-weight:600; color:#1a9c4a; }
.eco-tbl { width:100%; border-collapse:collapse; font-size:13px; }
.eco-tbl th { text-align:left; padding:8px 10px; background:#f6f8fa; font-size:10.5px;
    text-transform:uppercase; letter-spacing:.06em; color:#5c6b7a; border-bottom:1px solid #dde2e8; }
.eco-tbl td { padding:8px 10px; border-bottom:1px solid #edf0f3; }
.eco-hinweis { background:#eef4fb; border-left:3px solid #4a9eff; padding:11px 14px;
    border-radius:0 6px 6px 0; font-size:12.5px; line-height:1.65; color:#3a4a5a; }
.eco-fbtn { border:1px solid #dde2e8; background:#fff; color:#5c6b7a; padding:5px 12px;
    border-radius:15px; font-size:12px; cursor:pointer; font-family:inherit;
    transition:all .15s; }
.eco-fbtn:hover { border-color:#1a9c4a; color:#1a9c4a; }
.eco-fbtn.on { background:#1a9c4a; border-color:#1a9c4a; color:#fff; font-weight:600; }
</style>

<div class="eco-kopf">
  <!-- Haus mit Blitz als SVG — scharf in jeder Auflösung -->
  <svg class="eco-kopf-logo" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"
       aria-hidden="true">
    <polygon points="50,10 94,45 83,45 50,19 17,45 6,45" fill="#1a9c4a"/>
    <rect x="18" y="45" width="7" height="45" fill="#1a9c4a"/>
    <rect x="75" y="45" width="7" height="45" fill="#1a9c4a"/>
    <rect x="18" y="83" width="64" height="7" fill="#1a9c4a"/>
    <polygon points="58,52 45,52 38,73 48,73 43,86 62,64 51,64" fill="#1a9c4a"/>
  </svg>
  <div class="eco-kopf-text">
    <h1>eCharge@Home OCPP</h1>
    <p>OCPP 1.6-J Server für herstellerunabhängige Wallboxen — offen, ohne Cloud und lizenzfrei</p>
  </div>
  <div class="eco-kopf-status">
    <div class="zeile" style="color:__LAUFFARBE__;">● Dienst __LAUFTXT__</div>
    <div class="sub" id="kopf-sub">__KURZMELDUNG__</div>
  </div>
</div>

<div class="eco-tabs">
  <button class="eco-tab on" onclick="tab('u',this)">Übersicht</button>
  <button class="eco-tab" onclick="tab('e',this)">Einstellungen</button>
  <button class="eco-tab" onclick="tab('d',this)">Diagnose</button>
  <button class="eco-tab" onclick="tab('h',this)">Hilfe</button>
  <button class="eco-tab" onclick="tab('r',this)">Rechtliches</button>
</div>

<div class="eco-pane on" id="p-u">
  <div style="display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap;align-items:center;">
    <form method="post" style="margin:0"><input type="hidden" name="aktion" value="start">
      <button type="submit" class="__STARTCLS__" __STARTDIS__>▶ Starten</button></form>
    <form method="post" style="margin:0"><input type="hidden" name="aktion" value="restart">
      <button type="submit" class="eco-btn">↺ Neu starten</button></form>
    <form method="post" style="margin:0"><input type="hidden" name="aktion" value="stop">
      <button type="submit" class="eco-btn eco-btn-stop">■ Anhalten</button></form>

  </div>

  <div class="eco-grid">
    <div class="eco-tile"><div class="l">OCPP-Dienst</div>
      <div class="v" id="k-d">–</div><div class="s" id="k-ds">prüfe …</div></div>
    <div class="eco-tile"><div class="l">Wallboxen</div>
      <div class="v" id="k-w">–</div><div class="s" id="k-ws">–</div></div>
    <div class="eco-tile"><div class="l">Leistung</div>
      <div class="v" id="k-p">–</div><div class="s">kW</div></div>
    <div class="eco-tile"><div class="l">Ladungen</div>
      <div class="v" id="k-n">–</div><div class="s" id="k-e">–</div></div>
  </div>

  <!-- ── Verweis auf die Hauptanwendung ─────────────────────────────── -->
  <a class="eco-promo" href="https://www.loewemann.com" target="_blank"
     title="Mehr über eCharge@Home erfahren">
    <div class="eco-promo-logo">
      <svg viewBox="0 0 24 24" fill="none" stroke="#1a232d" stroke-width="2.2"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
      </svg>
    </div>
    <div class="eco-promo-text">
      <strong>eCharge@Home</strong>
      <span>Fahrtenbuch · Eigenbelege als PDF · Steuermodelle ·
            Vollkostenrechnung — <b style="color:#c8d8e8">loewemann.com</b></span>
    </div>
    <div class="eco-promo-arrow">›</div>
  </a>

  <!-- Scrollender Infoband -->
  <div class="eco-ticker-wrap">
    <div class="eco-ticker">
      &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
      <b>Updates</b> für dieses Plugin direkt über den LoxBerry Plugin-Manager
      &nbsp;·&nbsp;
      <b>eCharge@Home</b> — die passende Abrechnungssoftware zu diesem Plugin
      &nbsp;·&nbsp;
      Ladevorgänge erfassen über OCPP, Loxone-API oder CSV
      &nbsp;·&nbsp;
      Fahrtenbuch mit dienstlich / privat Zuordnung
      &nbsp;·&nbsp;
      Eigenbelege als PDF für Arbeitgeber und Finanzamt
      &nbsp;·&nbsp;
      Steuermodelle vergleichen — Pauschale, Auslagenersatz § 3 Nr. 50 EStG, Car Allowance
      &nbsp;·&nbsp;
      BMW CarData-Import — Fahrten direkt aus dem Fahrzeug
      &nbsp;·&nbsp;
      Läuft vollständig lokal · Docker · kein Abo · keine Cloud
      &nbsp;·&nbsp;
      <b>loewemann.com</b>
      &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
    </div>
  </div>

  <div class="eco-conn">
    <div><div class="l">OCPP — Wallbox verbinden mit</div>
      <div class="a" id="c-ws">…</div></div>
    <div><div class="l">eCharge@Home — Daten abholen von</div>
      <div class="a" id="c-api">…</div></div>
    <div><div class="l">eCharge@Home Verbindung</div>
      <div id="c-ech"><span class="eco-badge b-off">–</span></div></div>
    <div><div class="l">MQTT-Ausgabe</div>
      <div id="c-mq"><span class="eco-badge b-off">–</span></div></div>
  </div>

  <div class="eco-card">
    <h3>Letzte Ladevorgänge</h3>
    <div class="u">Erfasste Zählerstände vor und nach jedem Ladevorgang.</div>
    <table class="eco-tbl"><thead><tr>
      <th>Beginn</th><th>Ende</th><th>Ladepunkt</th>
      <th style="text-align:right">kWh</th><th style="text-align:right">Kosten</th>
    </tr></thead><tbody id="sess"><tr><td colspan="5">Lade …</td></tr></tbody></table>
  </div>
</div>

<div class="eco-pane" id="p-e">
  <form method="post">
  <input type="hidden" name="aktion" value="speichern">
  <div class="eco-card">
    <h3>Ports</h3>
    <div class="u">Beim Start wird automatisch der nächste freie Port verwendet, falls
      der gewünschte belegt ist.</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 20px;max-width:520px;">
      <div class="eco-f"><label>OCPP-Port (Wallboxen)</label>
        <input type="number" name="ocpp_port" min="1024" max="65535" value="__P_OCPP__">
        <div class="h">Standard 9000</div></div>
      <div class="eco-f"><label>Port der Schnittstelle</label>
        <input type="number" name="http_port" min="1024" max="65535" value="__P_HTTP__">
        <div class="h">Standard 8042</div></div>
    </div>
    <div class="eco-f"><label>Strompreis (€/kWh)</label>
      <input type="number" name="price_per_kwh" step="0.0001" value="__P_PREIS__" style="max-width:160px">
      <div class="h">Für die Abrechnung ist der Wert in eCharge@Home maßgeblich.</div></div>
  </div>

  <div class="eco-card">
    <h3>MQTT-Ausgabe</h3>
    <div class="u">Veröffentlicht alle Messwerte: Leistung, Zähler, Strom je Phase,
      Spannung, Ladestand und abgeschlossene Ladungen.</div>
    <!-- Als auffälliger Kasten, nicht als beiläufiges Kästchen: Das ist der
         Schalter, der über die ganze Funktion entscheidet. -->
    <label class="eco-schalter">
      <input type="checkbox" name="mqtt_aktiv" value="1" __MQTT_AN__>
      <span class="eco-schalter-box"></span>
      <span>
        <b>MQTT-Ausgabe einschalten</b><br>
        <span style="font-size:12px;color:#8896a3;">Ohne Haken wird nichts veröffentlicht.</span>
      </span>
    </label>
    <div class="eco-f"><label>Themenpräfix</label>
      <input type="text" name="mqtt_topic" value="__M_TOPIC__">
      <div class="h">z. B. <code>echargeocpp/WB1/leistung_kw</code></div></div>
    <div style="display:grid;grid-template-columns:2fr 1fr;gap:0 16px;max-width:520px;">
      <div class="eco-f"><label>Broker-Adresse</label>
        <input type="text" name="mqtt_host" value="__M_HOST__" placeholder="leer = MQTT-Gateway">
        <div class="h"><b>Leer lassen</b> wenn das MQTT-Gateway eingerichtet ist.</div></div>
      <div class="eco-f"><label>Port</label>
        <input type="number" name="mqtt_port" value="__M_PORT__"></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px;max-width:520px;">
      <div class="eco-f"><label>Benutzername</label>
        <input type="text" name="mqtt_user" value="__M_USER__"></div>
      <div class="eco-f"><label>Passwort</label>
        <input type="password" name="mqtt_pass" value="__M_PASS__"></div>
    </div>
  </div>
  <button type="submit" class="eco-btn eco-btn-start">Speichern und neu starten</button>
  </form>
</div>

<div class="eco-pane" id="p-d">
  <!-- Lastprüfung: Die häufigste Rückfrage bei einem LoxBerry ist, ob ein
       Plugin das Gerät auslastet. Statt zu diskutieren wird gemessen. -->
  <div class="eco-card">
    <h3>Systemlast prüfen</h3>
    <div class="u">Misst 30 Sekunden und nennt die größten Verbraucher —
      damit lässt sich klären, ob dieses Plugin Rechenzeit belegt oder
      etwas anderes.</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
      <button class="eco-btn" onclick="lastPruefen()">Messung starten</button>
    </div>
    <div id="last-ergebnis" style="margin-top:12px;"></div>
  </div>

  <div class="eco-card" id="rueck-karte" style="display:__RUECK_ANZEIGE__;
       border-left:3px solid __RUECK_FARBE__;">
    <h3>Letzte Aktion</h3>
    <div class="u">Rückmeldung des Startskripts.</div>
    <div style="background:#0d1117;color:#e6edf3;border-radius:7px;padding:12px 14px;
         font-family:'SF Mono',Menlo,Consolas,monospace;font-size:12px;
         line-height:1.7;white-space:pre-wrap;">__RUECKMELDUNG__</div>
  </div>

  <div class="eco-card">
    <h3>Logdatei</h3>
    <div class="u">Vollständiges Protokoll im LoxBerry-Logviewer — mit Suche,
      Herunterladen und allen älteren Einträgen.</div>
    <a href="__LOGVIEWER__" target="_blank" class="eco-btn"
       style="text-decoration:none;display:inline-block;">Logdatei öffnen</a>
    <div style="margin-top:8px;font-size:11.5px;color:#8896a3;font-family:monospace;">
      __LOGPFAD__</div>
  </div>

  <div class="eco-card">
    <h3>Aktuelle Ports</h3>
    <table class="eco-tbl" style="max-width:400px">
      <tr><td>OCPP-Port</td><td style="font-family:monospace" id="i-o">__L_OCPP__</td></tr>
      <tr><td>Schnittstelle</td><td style="font-family:monospace" id="i-h">__L_HTTP__</td></tr>
    </table>
  </div>
  <div class="eco-card">
    <h3>Testladungen</h3>
    <div class="u">Legt Beispiel-Ladevorgänge an — zum Prüfen der Anzeige, der
      Abrechnung und vor allem der MQTT-Ausgabe, ohne ein Fahrzeug anzustecken.
      Alle Testeinträge tragen den Ladepunkt <code>SIM-1</code> und lassen sich
      vollständig wieder entfernen.</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
      <button class="eco-btn" onclick="simulieren(3)">3 Ladungen anlegen</button>
      <button class="eco-btn" onclick="simulieren(10)">10 Ladungen anlegen</button>
      <button class="eco-btn eco-btn-stop" onclick="simLoeschen()">Testladungen entfernen</button>
    </div>
    <div id="sim-ergebnis" style="margin-top:10px;font-size:13px;"></div>
  </div>

  <div class="eco-card" style="border-left:3px solid #c0392b;">
    <h3>Daten zurücksetzen</h3>
    <div class="u">Entfernt Einträge dauerhaft. Es gibt keine Wiederherstellung.</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
      <button class="eco-btn eco-btn-stop" onclick="protokollLeeren()">Protokoll leeren</button>
      <button class="eco-btn eco-btn-stop" onclick="ladungenLoeschen()">Alle Ladevorgänge löschen</button>
    </div>
    <div id="reset-ergebnis" style="margin-top:10px;font-size:13px;"></div>
  </div>

  <div class="eco-card">
    <h3>Protokoll</h3>
    <div class="u">Aktualisiert sich alle 5 Sekunden. Die Filterknöpfe blenden
      Meldungen aus, die gerade nicht interessieren.</div>

    <!-- Ein Protokoll statt zwei: Die getrennte Anzeige von Datenbank und
         Logdatei zeigte weitgehend dasselbe zweimal. Hier laufen beide
         Quellen zusammen, Duplikate werden entfernt. -->
    <div style="display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap;align-items:center;">
      <button class="eco-fbtn on" data-f="alle"     onclick="filter(this)">Alle</button>
      <button class="eco-fbtn"    data-f="OCPP"     onclick="filter(this)">OCPP</button>
      <button class="eco-fbtn"    data-f="API"      onclick="filter(this)">API</button>
      <button class="eco-fbtn"    data-f="MQTT"     onclick="filter(this)">MQTT</button>
      <button class="eco-fbtn"    data-f="System"   onclick="filter(this)">System</button>
      <span style="margin-left:auto;font-size:11.5px;color:#8896a3;" id="f-anzahl"></span>
    </div>

    <!-- overflow-y:auto statt hidden — sonst lässt sich das Protokoll nicht scrollen -->
    <div id="term-host" style="height:380px;border-radius:8px;overflow-y:auto;overflow-x:hidden;background:#0d1117;"></div>
  </div>
</div>

<div class="eco-pane" id="p-h">

  <div class="eco-card" style="border-left:3px solid #1a9c4a;">
    <h3 style="font-size:16px;">Systemübersicht</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      Das Plugin stellt einen lokalen <b>OCPP-Server</b> (Open Charge Point
      Protocol) auf dem LoxBerry bereit. Es fungiert als Schnittstelle zwischen
      herstellerunabhängigen Wallboxen und dem Loxone Miniserver oder
      Drittsystemen.
      <br><br>
      Eingehende Telemetrie- und Transaktionsdaten von Ladestationen werden
      über WebSocket entgegengenommen, lokal protokolliert und über
      standardisierte Schnittstellen (MQTT, REST/JSON) bereitgestellt.
    </div>

    <pre style="background:#0d1117;color:#e6edf3;border-radius:7px;padding:14px 16px;
         margin-top:14px;font-family:'SF Mono',Menlo,Consolas,monospace;font-size:11px;
         line-height:1.6;overflow-x:auto;">┌──────────────┐   OCPP 1.6-J    ┌──────────────────────┐   MQTT / JSON   ┌──────────────────────┐
│   Wallbox    │ ──────────────▶ │ LoxBerry OCPP Plugin │ ──────────────▶ │  Loxone Miniserver   │
│ (Beliebiges  │   (WebSocket)   │   (Lokaler Server)   │                 │ (PV-Regelung, Visu,  │
│  Fabrikat)   │                 └──────────────────────┘                 │  Energiemanager)     │
└──────────────┘                                                          └──────────────────────┘</pre>
  </div>

  <div class="eco-card">
    <h3>Funktion und Einsatzzweck</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      Viele Wallboxen bieten ab Werk keine direkte Loxone-Schnittstelle oder
      erfordern herstellereigene Cloud-Dienste. Das Plugin ermöglicht die
      standardisierte, rein lokale Datenerfassung beliebiger Fabrikate ohne
      externe Cloud-Abhängigkeiten.
    </div>

    <div style="margin-top:16px;font-weight:600;color:#1a232d;font-size:13.5px;">
      Erfasste Datenpunkte</div>
    <table class="eco-tbl" style="margin-top:8px;">
      <tbody>
        <tr><td style="width:190px;"><b>Transaktionsdaten</b></td>
            <td>Startzeit, Endzeit, Zählerstand vor und nach der Ladung,
                geladene Gesamtenergie</td></tr>
        <tr><td><b>Live-Messwerte</b><br>
                <span style="font-size:11.5px;color:#8896a3;">zyklisch, Standard 15 s</span></td>
            <td>Wirkleistung (kW), Spannung (V), Phasenströme L1/L2/L3 (A),
                SoC/Ladestand (%), Autorisierungs-ID (RFID)</td></tr>
      </tbody>
    </table>

    <div style="margin-top:16px;font-weight:600;color:#1a232d;font-size:13.5px;">
      Bereitstellungskanäle</div>
    <table class="eco-tbl" style="margin-top:8px;">
      <tbody>
        <tr><td style="width:190px;"><b>MQTT</b></td>
            <td>Zyklisches Publishing sämtlicher Telemetriewerte mit
                <code>retain</code>-Flag für Loxone Miniserver, Home Assistant
                und Node-RED</td></tr>
        <tr><td><b>REST API</b></td>
            <td>Abruf historischer Transaktionsdaten und Systemzustände via
                HTTP/JSON</td></tr>
        <tr><td><b>Weboberfläche</b></td>
            <td>Lokale Einsicht in aktive Verbindungen, Ladevorgänge und
                Diagnose</td></tr>
      </tbody>
    </table>
  </div>

  <div class="eco-card">
    <h3>Kompatibilität</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      Unterstützt werden alle Ladestationen mit Implementierung von
      <b>OCPP 1.6-J</b> (JSON über WebSockets).
    </div>
    <div style="margin-top:12px;font-size:13px;line-height:1.9;color:#3a4a5a;">
      <b>Unterstützte Fabrikate (Auszug):</b><br>
      Easee (Home/Charge) · go-e Charger (HOMEfix/Gemini) · KEBA KeContact
      (P30 c-/x-series) · Alfen (Eve Single/Pro) · ABL (eMH1/eMH2) · Zaptec
      (Go/Pro) · Webasto (Live) · Wallbox (Pulsar Plus) · Mennekes (Amtron) ·
      Compleo · Vestel (EVC04) · Autel (MaxiCharger) · Heidelberg Energy
      Control (mit OCPP-Modul)
    </div>
    <div class="eco-hinweis" style="margin-top:14px;">
      <b>Hinweis zu Loxone-Wallboxen:</b> Loxone-eigene Wallboxen kommunizieren
      nativ mit dem Miniserver und benötigen diesen OCPP-Dienst nicht. Der
      Parallelbetrieb von Loxone- und Drittanbieter-Wallboxen im selben
      Netzwerk ist möglich.
    </div>
  </div>

  <div class="eco-card">
    <h3>Konfiguration</h3>

    <div style="font-weight:600;color:#1a232d;font-size:13.5px;margin-bottom:10px;">
      1. Wallbox einrichten</div>
    <div class="eco-step"><div class="n">1</div><div class="t">
      Netzwerk- bzw. OCPP-Konfiguration der Wallbox öffnen.</div></div>
    <div class="eco-step"><div class="n">2</div><div class="t">
      Protokoll auf <b>OCPP 1.6-J</b> (JSON / WebSocket) einstellen.
      SOAP wird nicht unterstützt.</div></div>
    <div class="eco-step"><div class="n">3</div><div class="t">
      Server-URL definieren:<br><span class="eco-adr" id="h-ws">…</span></div></div>
    <div class="eco-step"><div class="n">4</div><div class="t">
      Charge-Point-ID / Kennung vergeben (z. B. <code>WB1</code>).</div></div>
    <div class="eco-step"><div class="n">5</div><div class="t">
      Authentifizierung (Benutzer/Passwort) leer lassen, speichern und
      Wallbox neu starten.</div></div>

    <div style="font-weight:600;color:#1a232d;font-size:13.5px;margin:20px 0 10px;">
      2. MQTT-Gateway aktivieren</div>
    <div class="eco-step"><div class="n">1</div><div class="t">
      Im Plugin unter <b>Einstellungen</b> die Option
      <b>MQTT-Ausgabe aktivieren</b>.</div></div>
    <div class="eco-step"><div class="n">2</div><div class="t">
      Bei installiertem LoxBerry MQTT-Gateway werden Broker-Adresse und
      Zugangsdaten automatisch bezogen.</div></div>
    <div class="eco-step"><div class="n">3</div><div class="t">
      Nach dem Speichern den Status unter <b>Diagnose</b> verifizieren.</div></div>
  </div>

  <div class="eco-card">
    <h3>MQTT-Schnittstelle</h3>
    <div class="u">Das Topic-Präfix ist konfigurierbar. <code>&lt;id&gt;</code>
      entspricht der Charge-Point-Kennung der Wallbox.</div>
    <table class="eco-tbl">
      <thead><tr><th>MQTT-Topic</th><th style="width:110px;">Datentyp</th><th>Beschreibung</th></tr></thead>
      <tbody>
        <tr><td><code>…/status</code></td><td>String</td>
            <td>Betriebsstatus des Plugins</td></tr>
        <tr><td><code>…/&lt;id&gt;/online</code></td><td>Boolean (0/1)</td>
            <td>Verbindungsstatus der Wallbox</td></tr>
        <tr><td><code>…/&lt;id&gt;/laedt</code></td><td>Boolean (0/1)</td>
            <td>Status Ladevorgang aktiv</td></tr>
        <tr><td><code>…/&lt;id&gt;/leistung_kw</code></td><td>Float</td>
            <td>Aktuelle Ladeleistung in Kilowatt</td></tr>
        <tr><td><code>…/&lt;id&gt;/zaehler_wh</code></td><td>Integer</td>
            <td>Absoluter Zählerstand in Wattstunden</td></tr>
        <tr><td><code>…/&lt;id&gt;/strom_a_l1</code></td><td>Float</td>
            <td>Stromstärke Phase 1 in Ampere (<code>_l2</code>, <code>_l3</code> analog)</td></tr>
        <tr><td><code>…/&lt;id&gt;/spannung_v</code></td><td>Float</td>
            <td>Netzspannung in Volt</td></tr>
        <tr><td><code>…/&lt;id&gt;/ladestand_prozent</code></td><td>Integer</td>
            <td>Fahrzeug-Batterieladestand (SoC in %, falls übermittelt)</td></tr>
        <tr><td><code>…/&lt;id&gt;/benutzer</code></td><td>String</td>
            <td>RFID-UID / Autorisierungs-Tag</td></tr>
        <tr><td><code>…/&lt;id&gt;/ereignis</code></td><td>String</td>
            <td>Letzter Statusbericht der Wallbox (z. B. Available, Charging)</td></tr>
        <tr><td><code>…/&lt;id&gt;/ladung</code></td><td>JSON-Objekt</td>
            <td>Zusammenfassung des letzten abgeschlossenen Ladevorgangs</td></tr>
      </tbody>
    </table>
  </div>

  <div class="eco-card">
    <h3>REST API</h3>
    <div class="u">Lokaler Zugriff ohne Authentifizierung.</div>
    <table class="eco-tbl">
      <thead><tr><th>Endpunkt</th><th style="width:80px;">Methode</th>
        <th>Beschreibung</th><th style="width:170px;">Parameter</th></tr></thead>
      <tbody>
        <tr><td><code>/api/status</code></td><td>GET</td>
            <td>Systemstatus, verbundene Ladepunkte</td><td>–</td></tr>
        <tr><td><code>/api/sessions</code></td><td>GET</td>
            <td>Historische Ladevorgänge abfragen</td>
            <td><code>seit=</code>, <code>limit=</code></td></tr>
        <tr><td><code>/api/config</code></td><td>GET</td>
            <td>Aktuelle Server-Konfiguration</td><td>–</td></tr>
        <tr><td><code>/api/config</code></td><td>POST</td>
            <td>Konfigurationsparameter aktualisieren</td><td>JSON-Payload</td></tr>
        <tr><td><code>/api/log</code></td><td>GET</td>
            <td>Aktuelle Systemmeldungen</td><td>–</td></tr>
      </tbody>
    </table>
  </div>

  <div class="eco-card">
    <h3>Datenweiterverarbeitung (optionales Frontend)</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      Die vom Plugin bereitgestellten Rohdaten können über die REST-API von
      externen Anwendungen ausgelesen werden.
      <br><br>
      Für die kaufmännische Aufbereitung existiert mit <b>eCharge@Home</b> ein
      separat erhältliches, lokales Frontend. Dieses ermöglicht unter anderem
      die automatisierte Erstellung von Kostenerstattungsbelegen für den
      Arbeitgeber, die Führung eines Fahrtenbuchs, detaillierte Ladekosten- und
      Fahrzeugübersichten sowie die Verwaltung mehrerer Wallboxen.
      <br><br>
      Die Nutzung dieser Erweiterung ist optional und nicht Bestandteil des
      kostenlosen OCPP-Plugins.
      <br><br>
      <a href="https://www.loewemann.com/" target="_blank"
         style="color:#1a9c4a;font-weight:600;">www.loewemann.com →</a>
    </div>
  </div>

  <div class="eco-card">
    <h3>Fehleranalyse</h3>
    <table class="eco-tbl">
      <thead><tr><th style="width:230px;">Problem</th>
        <th style="width:180px;">Ursache</th><th>Maßnahme</th></tr></thead>
      <tbody>
        <tr><td>Dienst startet nicht</td><td>Portkonflikt</td>
            <td>Unter Einstellungen den Standardport 9000 auf einen freien
                Port ändern.</td></tr>
        <tr><td>Keine Verbindung zur Wallbox</td><td>Fehlkonfiguration</td>
            <td>IP-Adresse, Port und Protokoll (OCPP 1.6-J / WebSocket) in der
                Wallbox prüfen.</td></tr>
        <tr><td>Verbindung steht, keine Live-Werte</td>
            <td>Übertragungslogik der Station</td>
            <td>Manche Hersteller senden Messwerte erst bei aktiver Ladung.
                Ladevorgang starten oder Testladung über Diagnose auslösen.</td></tr>
        <tr><td>„X von 4 Einstellungen übernommen"</td>
            <td>Kein Fehler</td>
            <td>Nicht jede Wallbox kennt jede OCPP-Einstellung. go-e etwa
                übernimmt zwei von vier und liefert trotzdem vollständige
                Messwerte. Solange Zählerstände im Protokoll erscheinen,
                arbeitet die Anbindung einwandfrei.</td></tr>
        <tr><td>Verbindung wird zurückgesetzt</td>
            <td>Netzwerk</td>
            <td>Einzelne Abbrüche sind normal — die Wallbox baut die Verbindung
                selbst wieder auf. Häufen sie sich, liegt es meist am WLAN am
                Standort der Wallbox.</td></tr>
        <tr><td>MQTT-Status meldet Fehler</td><td>Broker nicht erreichbar</td>
            <td>Status des LoxBerry MQTT-Gateways prüfen oder manuell Host/Port
                in den Plugin-Einstellungen hinterlegen.</td></tr>
        <tr><td>Miniserver empfängt keine Werte</td><td>Eingangskonfiguration</td>
            <td>Virtuelle HTTP-/MQTT-Eingänge in Loxone Config auf exakte
                Topic-Bezeichnung und Datentypen abgleichen.</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- ═══ RECHTLICHES ═══════════════════════════════════════════════════ -->
<div class="eco-pane" id="p-r">

  <div class="eco-card">
    <h3 style="font-size:16px;">Rechtliche Hinweise und Nutzungsbedingungen</h3>
  </div>

  <div class="eco-card">
    <h3>1. Gegenstand und Funktionsumfang</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      Das Plugin dient ausschließlich als technischer Dienst zur Entgegennahme,
      lokalen Speicherung und Bereitstellung von Ladedaten über definierte
      Schnittstellen (MQTT, REST/JSON).
      <br><br>
      Die Software führt <b>keine Datenanalysen, Auswertungen oder
      automatisierte Abrechnungen</b> durch. Die Weiterverarbeitung,
      Interpretation und Nutzung der bereitgestellten Daten obliegt
      vollumfänglich den angebundenen Folgesystemen und deren Betreibern.
    </div>
  </div>

  <div class="eco-card" style="border-left:3px solid #e8a33d;">
    <h3>2. Messgenauigkeit und Eichrecht</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      <b>Datenherkunft:</b> Die Software speichert die von der jeweiligen
      Wallbox per Protokoll übermittelten Rohdaten. Es erfolgt keine
      Verifikation, Korrektur oder messtechnische Eichung durch das Plugin.
      <br><br>
      <b>Abrechnungskonformität:</b> Die Einhaltung der Vorschriften des
      Mess- und Eichgesetzes (MessEG) für Abrechnungszwecke gegenüber Dritten
      oder dem Finanzamt hängt ausschließlich von der Zulassung, Hardware und
      Konfiguration der eingesetzten Wallbox ab. Das Plugin übernimmt keine
      Gewähr für die rechtliche Verwertbarkeit der Messdaten.
    </div>
  </div>

  <div class="eco-card" style="border-left:3px solid #e8a33d;">
    <h3>3. Technische und elektrische Sicherheit</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      <b>Passiver Betriebsmodus:</b> Das Plugin agiert primär als passiver
      Datenempfänger. Es steuert keine Schaltkontakte, regelt keine Ladeströme
      eigenständig und greift nicht aktiv in Steuer- oder
      Sicherheitsmechanismen der Ladeeinrichtung ein.
      <br><br>
      <b>Betreiberverantwortung:</b> Für die ordnungsgemäße Installation,
      Netzintegration, Absicherung und den sicheren Betrieb der
      Ladeinfrastruktur ist ausschließlich der Betreiber zuständig.
      Elektroinstallationsarbeiten dürfen nur durch qualifizierte
      Elektrofachkräfte gemäß den geltenden VDE-Vorschriften und TAB des
      Netzbetreibers ausgeführt werden.
    </div>
  </div>

  <div class="eco-card">
    <h3>4. Datenschutz und Datensicherheit</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      <b>Lokale Verarbeitung:</b> Das Plugin arbeitet vollständig autark auf
      dem Host-System. Es werden keine Telemetrie-, Diagnose- oder
      Nutzungsdaten an externe Server übertragen. Es besteht kein Cloud- oder
      Registrierungszwang.
      <br><br>
      <b>Speicherort:</b> Alle Transaktionsdaten verbleiben in der lokalen
      SQLite-Datenbank unter
      <code>/opt/loxberry/data/plugins/echargeocpp/ocpp.db</code>.
      <br><br>
      <b>Personenbezug (DSGVO):</b> Werden Ladedaten bestimmten Personen
      zugeordnet (z. B. über RFID-Karten-IDs oder Kennzeichen), ist der
      Anlagenbetreiber für die Einhaltung der datenschutzrechtlichen Vorgaben
      verantwortlich.
      <br><br>
      <b>Netzwerkweitergabe:</b> Bei aktivierter MQTT-Ausgabe werden Daten an
      den konfigurierten Broker weitergeleitet. Die Zugriffskontrolle und
      Absicherung des Datenverkehrs obliegt der Netzwerkkonfiguration des
      Betreibers.
    </div>
  </div>

  <div class="eco-card">
    <h3>5. Haftungsausschluss und Gewährleistung</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      <b>Überlassung:</b> Die Software wird unentgeltlich im vorliegenden
      Zustand bereitgestellt. Gewährleistungsansprüche bezüglich
      Funktionsfähigkeit, unterbrechungsfreiem Betrieb, Datenvollständigkeit
      oder Kompatibilität mit spezifischen Wallbox-Modellen sind
      ausgeschlossen.
      <br><br>
      <b>Haftungsmaßstab:</b> Da es sich um eine unentgeltliche Überlassung
      handelt, greifen die gesetzlichen Regelungen der Schenkung
      (§§ 521, 523, 524 BGB). Eine Haftung ist auf Vorsatz und grobe
      Fahrlässigkeit beschränkt.
      <br><br>
      <b>Folgeschäden:</b> Die Haftung für mittelbare Schäden, Folgeschäden,
      fehlerhafte Abrechnungen, Datenverlust, Produktionsausfälle oder
      entgangenen Gewinn ist im gesetzlich zulässigen Rahmen ausgeschlossen.
      <br><br>
      <b>Zwingendes Recht:</b> Gesetzlich zwingende Haftungstatbestände
      (z. B. nach dem Produkthaftungsgesetz oder bei Schäden aus der Verletzung
      des Lebens, des Körpers oder der Gesundheit) bleiben unberührt.
    </div>
  </div>

  <div class="eco-card">
    <h3>6. Nutzungs- und Lizenzrechte</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      <b>Gestattete Nutzung:</b> Die Installation, die private und gewerbliche
      Nutzung sowie die unveränderte Weitergabe dieser Software einschließlich
      aller Lizenz- und Rechtshinweise sind kostenfrei gestattet.
      <br><br>
      <b>Einschränkungen:</b> Die Modifikation, das Reverse-Engineering, die
      Dekompilierung, die Weiterverbreitung unter anderem Namen sowie die
      entgeltliche Veräußerung oder Einbindung in kommerzielle Softwarepakete
      bedürfen der vorherigen schriftlichen Zustimmung des Rechteinhabers.
      <br><br>
      <b>Urheberrecht:</b> Alle Rechte an der Software und den zugehörigen
      Dokumentationen verbleiben beim Urheber.
    </div>
  </div>

  <div class="eco-card">
    <h3>7. Standards und Marken</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      Die Implementierung nutzt offene Kommunikationsstandards ohne
      Fremdbibliotheken.
    </div>
    <table class="eco-tbl" style="margin-top:12px;">
      <thead><tr><th>Standard / Protokoll</th><th>Organisation</th>
        <th>Lizenzstatus</th></tr></thead>
      <tbody>
        <tr><td>OCPP 1.6-J</td><td>Open Charge Alliance (OCA)</td>
            <td>Offener, lizenzfreier Standard</td></tr>
        <tr><td>WebSocket (RFC 6455)</td><td>IETF</td>
            <td>Offener Internetstandard</td></tr>
        <tr><td>MQTT 3.1.1</td><td>OASIS</td>
            <td>Offener, lizenzfreier Standard</td></tr>
      </tbody>
    </table>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;margin-top:14px;">
      Die Nennung von geschützten Markennamen, Herstellern oder
      Produktbezeichnungen dient ausschließlich der technischen
      Kompatibilitätsbeschreibung. Sämtliche Marken sind Eigentum der
      jeweiligen Inhaber.
    </div>
  </div>

  <div class="eco-card">
    <h3>8. Schlussbestimmungen</h3>
    <div style="font-size:13.5px;line-height:1.8;color:#3a4a5a;">
      Es gilt das Recht der Bundesrepublik Deutschland unter Ausschluss des
      UN-Kaufrechts. Sollte eine Bestimmung unwirksam sein, bleibt die
      Wirksamkeit der übrigen unberührt.
      <br><br>
      <b>Loewemann IT Consulting</b><br>
      <a href="https://www.loewemann.com/" target="_blank"
         style="color:#1a9c4a;">www.loewemann.com</a>
    </div>
  </div>
</div>

<script>
let LOGZEILEN = __LOGJSON__;
var P = { ocpp: __L_OCPP__, http: __L_HTTP__ };

// Terminal-Stil, isoliert im Shadow DOM: LoxBerry-CSS kommt nicht rein.
var TCSS =
  ':host{display:block;background:#0d1117;}' +
  'table{width:100%;border-collapse:collapse;table-layout:fixed;}' +
  'col.a{width:70px}col.b{width:74px}' +
  'tr:nth-child(even){background:rgba(255,255,255,.03)}' +
  'tr:hover{background:#161b22}' +
  'td{padding:4px 6px;vertical-align:top;border:none;line-height:1.55;' +
  'font-family:"SF Mono","Consolas","Liberation Mono",Menlo,monospace;font-size:12px;}' +
  'td.a{color:#6e7681;padding-left:12px;white-space:nowrap;}' +
  'td.b{text-align:right;font-size:10px;font-weight:700;letter-spacing:.05em;' +
  'text-transform:uppercase;padding-right:8px;white-space:nowrap;color:#8b949e;}' +
  'td.c{color:#e6edf3;padding-right:12px;word-break:break-word;}' +
  'tr.ok td.c,tr.ok td.b{color:#3fb950}' +
  'tr.wn td.c,tr.wn td.b{color:#d29922}' +
  'tr.er td.c,tr.er td.b{color:#f85149}' +
  'td.b.mqtt{color:#a371f7}td.b.api{color:#58a6ff}td.b.ocpp{color:#79c0ff}' +
  'tr.ok td.b.mqtt,tr.ok td.b.api,tr.ok td.b.ocpp{color:#3fb950}' +
  'tr.wn td.b.mqtt,tr.wn td.b.api,tr.wn td.b.ocpp{color:#d29922}' +
  '.leer{padding:26px 14px;color:#6e7681;text-align:center;font-size:12px;' +
  'font-family:"SF Mono",monospace;}';

function shadow(id) {
  var h = document.getElementById(id);
  if (!h) return null;
  if (!h.__s) {
    var s = h.attachShadow({mode:'open'});
    var st = document.createElement('style');
    st.textContent = TCSS;
    s.appendChild(st);
    var w = document.createElement('div');
    s.appendChild(w);
    h.__s = s; h.__w = w;
  }
  return h.__w;
}

function tabelle(zeilen) {
  return '<table><colgroup><col class="a"><col class="b"><col></colgroup><tbody>'
       + zeilen.join('') + '</tbody></table>';
}

function tab(n, b) {
  document.querySelectorAll('.eco-pane').forEach(function(p){ p.classList.remove('on'); });
  document.querySelectorAll('.eco-tab').forEach(function(x){ x.classList.remove('on'); });
  document.getElementById('p-' + n).classList.add('on');
  b.classList.add('on');
  if (n === 'd') logZeigen();
}

function basis() {
  return location.protocol + '//' + location.hostname + ':' + P.http;
}

function zahl(v, s) {
  s = s || 0;
  return v == null ? '–' : Number(v).toLocaleString('de-DE',
    {minimumFractionDigits:s, maximumFractionDigits:s});
}

// Zeitstempel "2026-08-24 20:34:31" in Sekunden umrechnen — ohne Date(),
// damit keine Zeitzonenvermutung dazwischenfunkt.
function sekunden(ts) {
  var m = (ts||'').match(/(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d):(\d\d)/);
  if (!m) return 0;
  return Date.UTC(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]) / 1000;
}

function badge(t, k) { return '<span class="eco-badge b-' + k + '">' + t + '</span>'; }

function adressen() {
  var h = location.hostname;
  var ws = 'ws://' + h + ':' + P.ocpp + '/ocpp';
  var api = h + ':' + P.http;
  ['c-ws','h-ws'].forEach(function(i){ var e=document.getElementById(i); if(e) e.textContent=ws; });
  ['c-api','h-api'].forEach(function(i){ var e=document.getElementById(i); if(e) e.textContent=api; });
  var o=document.getElementById('i-o'); if(o) o.textContent=P.ocpp;
  var p=document.getElementById('i-h'); if(p) p.textContent=P.http;
}

// Alle Meldungen — aus Datenbank und Logdatei zusammengeführt
var ALLE = [];
var FILTER = 'alle';

// Zeilen aus der Logdatei in dieselbe Form bringen wie die aus der Datenbank
LOGZEILEN.forEach(function(z) {
  var m = z.match(/^(\S+ \S+)\s*\[?(\w+)\]?\s*(\w+):\s*(.*)$/);
  if (!m) return;
  ALLE.push({ timestamp: m[1], level: m[2], source: m[3], message: m[4] });
});

function filter(btn) {
  document.querySelectorAll('.eco-fbtn').forEach(function(b){ b.classList.remove('on'); });
  btn.classList.add('on');
  FILTER = btn.dataset.f;
  protokollZeigen();
}

function protokollZeigen() {
  var w = shadow('term-host');
  if (!w) return;

  // Doppelte entfernen: Dieselbe Meldung steht oft in Datenbank UND Logdatei
  var gesehen = {};
  var liste = ALLE.filter(function(m) {
    var k = (m.timestamp||'') + '|' + (m.message||'');
    if (gesehen[k]) return false;
    gesehen[k] = 1;
    return true;
  }).filter(function(m) {
    if (FILTER === 'alle') return true;
    return m.source === FILTER;
  }).filter(function(m) {
    return !/^=+$/.test(m.message || '');
  });

  // Neueste zuletzt, damit man wie in einer Logdatei nach unten liest
  liste.sort(function(a,b){ return (a.timestamp||'') < (b.timestamp||'') ? -1 : 1; });

  var anz = document.getElementById('f-anzahl');
  if (anz) anz.textContent = liste.length + ' Meldungen';

  if (!liste.length) {
    w.innerHTML = '<div class="leer">Keine Meldungen für diesen Filter.</div>';
    return;
  }

  var zeilen = liste.map(function(m) {
    var t = m.message || '';
    var art = (m.level === 'WARN') ? 'wn'
            : (m.level === 'CRITICAL' || m.level === 'CRIT') ? 'er'
            : /bereit|angemeldet|beendet|begonnen|abgerufen|Verbindung|Konfigur/i.test(t) ? 'ok' : '';
    var qc = {MQTT:'mqtt', API:'api', OCPP:'ocpp'}[m.source] || '';
    return '<tr class="' + art + '"><td class="a">' + (m.timestamp||'').slice(11,19) + '</td>'
         + '<td class="b ' + qc + '">' + (m.source||'') + '</td>'
         + '<td class="c">' + t.replace(/</g,'&lt;') + '</td></tr>';
  });
  var host = document.getElementById('term-host');
  var unten = host.scrollHeight - host.scrollTop - host.clientHeight < 60;
  w.innerHTML = tabelle(zeilen);
  if (unten) host.scrollTop = host.scrollHeight;
}

function logZeigen() { protokollZeigen(); }

async function ports() {
  for (var i = 0; i < 4; i++) {
    var p = 8042 + i;
    try {
      var r = await fetch(location.protocol + '//' + location.hostname + ':' + p + '/api/status');
      var d = await r.json();
      P = { ocpp: d.ocpp_port || 9000, http: d.http_port || p };
      break;
    } catch (e) {}
  }
  adressen();
}

async function status() {
  var d;
  try {
    d = await (await fetch(basis() + '/api/status')).json();
  } catch (e) {
    document.getElementById('k-d').textContent = 'gestoppt';
    document.getElementById('k-d').style.color = '#c0392b';
    document.getElementById('k-ds').textContent = 'nicht erreichbar';
    document.getElementById('c-ech').innerHTML = badge('–','off');
    document.getElementById('c-mq').innerHTML = badge('–','off');
    protokollZeigen();   // die Logdatei kann trotzdem etwas enthalten
    return;
  }

  if (d.ocpp_port) { P = {ocpp:d.ocpp_port, http:d.http_port}; adressen(); }
  var o = d.ocpp || {};

  var kd = document.getElementById('k-d');
  if (o.laeuft) { kd.textContent='bereit'; kd.style.color='#1a9c4a';
    document.getElementById('k-ds').textContent = 'Port ' + P.ocpp; }
  else if (o.ocpp_fehler) { kd.textContent='eingeschränkt'; kd.style.color='#e8a33d';
    document.getElementById('k-ds').textContent = 'kein Wallbox-Empfang'; }
  else { kd.textContent='gestoppt'; kd.style.color='#c0392b';
    document.getElementById('k-ds').textContent = 'nicht bereit'; }

  document.getElementById('k-w').textContent = o.verbunden || 0;
  document.getElementById('k-ws').textContent = o.laedt ? '⚡ lädt' :
    (o.verbunden ? 'verbunden' : 'keine');
  document.getElementById('k-p').textContent = zahl(o.leistung_kw, 1);
  document.getElementById('k-n').textContent = d.sessions_gesamt || 0;
  document.getElementById('k-e').textContent = zahl(d.kwh_gesamt, 1) + ' kWh';

  var meld = d.letzte_meldungen || [];
  var api = meld.slice().reverse().filter(function(m){ return m.source === 'API'; })[0];
  if (api && d.jetzt) {
    // Gegen die Uhr des Dienstes rechnen, nicht gegen die des Browsers:
    // Der Zeitstempel trägt keine Zeitzone, und Browser deuten das
    // unterschiedlich. Daher stand dort "zuletzt 18:38" statt 20:38.
    var sek = Math.max(0, sekunden(d.jetzt) - sekunden(api.timestamp));
    var uhr = api.timestamp.slice(11,16);
    document.getElementById('c-ech').innerHTML = sek < 180
      ? badge('● verbunden ' + uhr, 'ok')
      : sek < 3600 ? badge('zuletzt ' + uhr + ' (' + Math.round(sek/60) + ' Min)', 'wn')
      : badge('zuletzt ' + uhr, 'wn');
  } else {
    document.getElementById('c-ech').innerHTML = badge('kein Abruf', 'off');
  }

  // MQTT: eingeschaltet ist nicht dasselbe wie verbunden
  var mq = document.getElementById('c-mq');
  if (!d.mqtt_aktiv)          mq.innerHTML = badge('ausgeschaltet','off');
  else if (d.mqtt_verbunden)  mq.innerHTML = badge('● verbunden' +
                                (d.mqtt_broker ? ' · ' + d.mqtt_broker : ''), 'ok');
  else                        mq.innerHTML = badge('eingeschaltet, kein Broker','wn');

  // Neue Meldungen aufnehmen; die Anzeige übernimmt protokollZeigen()
  var bekannt = {};
  ALLE.forEach(function(m){ bekannt[(m.timestamp||'')+'|'+(m.message||'')] = 1; });
  meld.forEach(function(m) {
    if (!bekannt[(m.timestamp||'')+'|'+(m.message||'')]) ALLE.push(m);
  });
  if (ALLE.length > 400) ALLE = ALLE.slice(-400);
  protokollZeigen();
}

async function sessions() {
  try {
    var d = await (await fetch(basis() + '/api/sessions?limit=10')).json();
    var tb = document.getElementById('sess');
    if (!d.sessions || !d.sessions.length) {
      tb.innerHTML = '<tr><td colspan="5" style="color:#8896a3">Noch keine Ladevorgänge.</td></tr>';
      return;
    }
    tb.innerHTML = d.sessions.map(function(s) {
      var eur = (s.energy_kwh * (s.price_per_kwh||0)).toFixed(2);
      return '<tr><td>' + (s.start_time||'').slice(0,16) + '</td>'
        + '<td>' + ((s.end_time||'').slice(11,16) || '–') + '</td>'
        + '<td>' + (s.meter_id||'–') + '</td>'
        + '<td style="text-align:right;font-family:monospace">' + zahl(s.energy_kwh,2) + '</td>'
        + '<td style="text-align:right;font-family:monospace">' + eur + ' €</td></tr>';
    }).join('');
  } catch (e) {}
}

async function simulieren(n) {
  var out = document.getElementById('sim-ergebnis');
  out.innerHTML = '<span style="color:#8896a3">Lege Testladungen an …</span>';
  try {
    var r = await fetch(basis() + '/api/simulation', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({anzahl:n})
    });
    var d = await r.json();
    out.innerHTML = '<span style="color:#1a9c4a">✓ ' + d.erzeugt
      + ' Testladungen angelegt.</span> Sie erscheinen unter Übersicht und wurden '
      + 'über MQTT veröffentlicht (falls eingeschaltet).';
    sessions();
  } catch (e) {
    out.innerHTML = '<span style="color:#c0392b">Dienst nicht erreichbar.</span>';
  }
}

async function simLoeschen() {
  var out = document.getElementById('sim-ergebnis');
  out.innerHTML = '<span style="color:#8896a3">Entferne …</span>';
  try {
    var r = await fetch(basis() + '/api/simulation/loeschen', {method:'POST'});
    var d = await r.json();
    out.innerHTML = '<span style="color:#1a9c4a">✓ ' + d.geloescht
      + ' Testladungen entfernt.</span>';
    sessions();
  } catch (e) {
    out.innerHTML = '<span style="color:#c0392b">Dienst nicht erreichbar.</span>';
  }
}

async function protokollLeeren() {
  if (!confirm('Das gesamte Protokoll wird gelöscht — auch die Logdatei. Fortfahren?')) return;
  var out = document.getElementById('reset-ergebnis');
  out.innerHTML = '<span style="color:#8896a3">Leere Protokoll …</span>';
  try {
    var d = await (await fetch(basis() + '/api/protokoll/loeschen', {method:'POST'})).json();
    out.innerHTML = '<span style="color:#1a9c4a">✓ ' + d.geloescht + ' Einträge entfernt.</span>';
    ALLE = [];           // auch die im Browser gehaltenen Zeilen verwerfen
    LOGZEILEN = [];
    protokollZeigen();
  } catch (e) {
    out.innerHTML = '<span style="color:#c0392b">Dienst nicht erreichbar.</span>';
  }
}

async function ladungenLoeschen() {
  if (!confirm('ALLE Ladevorgänge werden gelöscht — auch echte, nicht nur Testdaten.\n\nFortfahren?')) return;
  var out = document.getElementById('reset-ergebnis');
  out.innerHTML = '<span style="color:#8896a3">Lösche …</span>';
  try {
    var d = await (await fetch(basis() + '/api/ladungen/loeschen', {method:'POST'})).json();
    out.innerHTML = '<span style="color:#1a9c4a">✓ ' + d.geloescht + ' Ladevorgänge gelöscht.</span>';
    sessions();
  } catch (e) {
    out.innerHTML = '<span style="color:#c0392b">Dienst nicht erreichbar.</span>';
  }
}

async function lastPruefen() {
  var out = document.getElementById('last-ergebnis');
  out.innerHTML = '<span style="color:#8896a3">Messe 30 Sekunden — bitte warten …</span>';
  try {
    var r = await fetch(basis() + '/api/systemlast', {method:'POST'});
    var d = await r.json();
    out.innerHTML =
      '<pre style="background:#0d1117;color:#e6edf3;border-radius:7px;'
      + 'padding:14px 16px;font-family:\'SF Mono\',Menlo,Consolas,monospace;'
      + 'font-size:11.5px;line-height:1.55;white-space:pre-wrap;overflow-x:auto;'
      + 'max-height:520px;">' + (d.ausgabe || 'Keine Ausgabe') + '</pre>';
  } catch (e) {
    out.innerHTML = '<span style="color:#c0392b">Dienst nicht erreichbar.</span>';
  }
}

adressen();
ports().then(function(){ status(); sessions(); });
setInterval(status, 5000);
setInterval(sessions, 20000);
</script>
HTML

my $logpfad = "$logdir/ocpp.log";
my $logviewer = "/admin/system/tools/logfile.cgi?logfile="
              . $logpfad . "&header=html&format=template&only=once";
$html =~ s/__LOGVIEWER__/$logviewer/;
$html =~ s|__LOGPFAD__|$logpfad|;
$html =~ s/__LOGJSON__/$log_json/;
$html =~ s/__L_OCPP__/$lp{ocpp}/g;
$html =~ s/__L_HTTP__/$lp{http}/g;
$html =~ s/__P_OCPP__/$kfg{ocpp_port}/;
$html =~ s/__P_HTTP__/$kfg{http_port}/;
$html =~ s/__P_PREIS__/$kfg{price_per_kwh}/;
$html =~ s/__MQTT_AN__/$kfg{mqtt_aktiv} ? 'checked' : ''/e;
$html =~ s/__M_TOPIC__/$kfg{mqtt_topic}/;
$html =~ s/__M_HOST__/$kfg{mqtt_host}/;
$html =~ s/__M_PORT__/$kfg{mqtt_port}/;
$html =~ s/__M_USER__/$kfg{mqtt_user}/;
$html =~ s/__M_PASS__/$kfg{mqtt_pass}/;
$html =~ s/__STARTCLS__/$start_cls/;
$html =~ s/__STARTDIS__/$start_dis/;
$html =~ s/__LAUFTXT__/$lauf_txt/g;
$html =~ s/__KURZMELDUNG__/$kurzmeldung/;
my $r_anzeige = $meldung ? 'block' : 'none';
my %rf = (ok => '#1a9c4a', warn => '#e8a33d', err => '#c0392b');
my $r_farbe = $rf{$art} || '#8896a3';
my $r_text = $meldung || '';
$r_text =~ s/[<>]//g;
$html =~ s/__RUECK_ANZEIGE__/$r_anzeige/;
$html =~ s/__RUECK_FARBE__/$r_farbe/;
$html =~ s|__RUECKMELDUNG__|$r_text|;
$html =~ s/__LAUFFARBE__/$lauf_farbe/g;
print $html;

LOGEND();
LoxBerry::Web::lbfooter();
exit;
