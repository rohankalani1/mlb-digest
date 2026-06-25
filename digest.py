import os
import re
import html
import sys
import time
import smtplib
import subprocess
import statsapi
from collections import Counter
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

TEAM_NICKNAMES = {
    'Arizona Diamondbacks':  'D-backs',
    'Atlanta Braves':        'Braves',
    'Baltimore Orioles':     'Orioles',
    'Boston Red Sox':        'Red Sox',
    'Chicago Cubs':          'Cubs',
    'Chicago White Sox':     'White Sox',
    'Cincinnati Reds':       'Reds',
    'Cleveland Guardians':   'Guardians',
    'Colorado Rockies':      'Rockies',
    'Detroit Tigers':        'Tigers',
    'Houston Astros':        'Astros',
    'Kansas City Royals':    'Royals',
    'Los Angeles Angels':    'Angels',
    'Los Angeles Dodgers':   'Dodgers',
    'Miami Marlins':         'Marlins',
    'Milwaukee Brewers':     'Brewers',
    'Minnesota Twins':       'Twins',
    'New York Mets':         'Mets',
    'New York Yankees':      'Yankees',
    'Athletics':             "A's",
    'Philadelphia Phillies': 'Phillies',
    'Pittsburgh Pirates':    'Pirates',
    'San Diego Padres':      'Padres',
    'San Francisco Giants':  'Giants',
    'Seattle Mariners':      'Mariners',
    'St. Louis Cardinals':   'Cardinals',
    'Tampa Bay Rays':        'Rays',
    'Texas Rangers':         'Rangers',
    'Toronto Blue Jays':     'Blue Jays',
    'Washington Nationals':  'Nationals',
}

TEAM_ABBREVS = {
    'Arizona Diamondbacks':  'ARI', 'Atlanta Braves':        'ATL',
    'Baltimore Orioles':     'BAL', 'Boston Red Sox':        'BOS',
    'Chicago Cubs':          'CHC', 'Chicago White Sox':     'CWS',
    'Cincinnati Reds':       'CIN', 'Cleveland Guardians':   'CLE',
    'Colorado Rockies':      'COL', 'Detroit Tigers':        'DET',
    'Houston Astros':        'HOU', 'Kansas City Royals':    'KC',
    'Los Angeles Angels':    'LAA', 'Los Angeles Dodgers':   'LAD',
    'Miami Marlins':         'MIA', 'Milwaukee Brewers':     'MIL',
    'Minnesota Twins':       'MIN', 'New York Mets':         'NYM',
    'New York Yankees':      'NYY', 'Athletics':             'ATH',
    'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates':    'PIT',
    'San Diego Padres':      'SD',  'San Francisco Giants':  'SF',
    'Seattle Mariners':      'SEA', 'St. Louis Cardinals':   'STL',
    'Tampa Bay Rays':        'TB',  'Texas Rangers':         'TEX',
    'Toronto Blue Jays':     'TOR', 'Washington Nationals':  'WSH',
}

TEAM_COLORS = {
    'Arizona Diamondbacks': '#A71930', 'Atlanta Braves':       '#CE1141',
    'Baltimore Orioles':    '#DF4601', 'Boston Red Sox':       '#BD3039',
    'Chicago Cubs':         '#0E3386', 'Chicago White Sox':    '#27251F',
    'Cincinnati Reds':      '#C6011F', 'Cleveland Guardians':  '#00385D',
    'Colorado Rockies':     '#33006F', 'Detroit Tigers':       '#0C2340',
    'Houston Astros':       '#002D62', 'Kansas City Royals':   '#004687',
    'Los Angeles Angels':   '#BA0021', 'Los Angeles Dodgers':  '#005A9C',
    'Miami Marlins':        '#00A3E0', 'Milwaukee Brewers':    '#12284B',
    'Minnesota Twins':      '#002B5C', 'New York Mets':        '#002D72',
    'New York Yankees':     '#003087', 'Athletics':            '#003831',
    'Philadelphia Phillies':'#E81828', 'Pittsburgh Pirates':   '#FDB827',
    'San Diego Padres':     '#2F241D', 'San Francisco Giants': '#FD5A1E',
    'Seattle Mariners':     '#0C2C56', 'St. Louis Cardinals':  '#C41E3A',
    'Tampa Bay Rays':       '#092C5C', 'Texas Rangers':        '#003278',
    'Toronto Blue Jays':    '#134A8E', 'Washington Nationals': '#AB0003',
}

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")


def get_output_dir():
    """Return the directory where HTML files are saved.
    GitHub Actions: same dir as the script (repo root).
    Local: 'digests/' subfolder if it exists, otherwise script dir."""
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return script_dir
    digests_sub = os.path.join(script_dir, 'digests')
    return digests_sub if os.path.isdir(digests_sub) else script_dir


def normalize_name(name):
    """Convert 'Last, F.' box score format to 'F. Last' display format."""
    if not name or ',' not in name:
        return name
    last, first = name.split(',', 1)
    return f"{first.strip()} {last.strip()}"


def _ascii(s):
    """Lowercase + strip diacritics for accent-insensitive name matching."""
    import unicodedata
    return ''.join(
        c for c in unicodedata.normalize('NFD', s.lower())
        if unicodedata.category(c) != 'Mn'
    )


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def get_target_date(override=None):
    """Return (MM/DD/YYYY, 'Month DD, YYYY') for yesterday, or an override date."""
    if override:
        dt = datetime.strptime(override, "%m/%d/%Y")
    else:
        dt = datetime.now() - timedelta(days=1)
    return dt.strftime("%m/%d/%Y"), dt.strftime("%B %d, %Y")


# ---------------------------------------------------------------------------
# MLB data
# ---------------------------------------------------------------------------

def fetch_completed_games(date_str):
    """Return only Final/Completed games for a given date."""
    try:
        schedule = statsapi.schedule(date=date_str)
    except Exception as e:
        print(f"  [warn] Could not fetch schedule: {e}")
        return []
    return [g for g in schedule if g.get("status") in ("Final", "Game Over", "Completed Early")]


def fetch_team_records(year):
    """Return (records dict, streaks dict) mapping team_id (int) -> string."""
    try:
        raw = statsapi.get('standings', {
            'leagueId': '103,104',
            'season': str(year),
            'standingsTypes': 'regularSeason',
        })
        records = {}
        streaks = {}
        for division in raw['records']:
            for tr in division['teamRecords']:
                team_id = tr['team']['id']
                records[team_id] = f"{tr['wins']}-{tr['losses']}"
                streaks[team_id] = tr.get('streak', {}).get('streakCode', '')
        return records, streaks
    except Exception as e:
        print(f"  [warn] Could not fetch standings: {e}")
        return {}, {}


def get_box_data(game_pk):
    try:
        return statsapi.boxscore_data(game_pk)
    except Exception as e:
        print(f"  [warn] Could not fetch box score for {game_pk}: {e}")
        return None


def detect_doubleheaders(games):
    """Tag games that are part of a doubleheader with 'Game N of 2'."""
    counts = Counter((g["away_name"], g["home_name"]) for g in games)
    trackers = {}
    for g in games:
        key = (g["away_name"], g["home_name"])
        if counts[key] > 1:
            trackers[key] = trackers.get(key, 0) + 1
            g["_game_num"] = f"Game {trackers[key]} of {counts[key]}"
    return games


def extract_player_notes(box_data, away_name, home_name, away_score=0, home_score=0):
    """Pull pitching decisions (+ quality start flag) and standout batting lines."""
    if not box_data:
        return ""

    lines = []

    # Pitching: always include both starters; include relievers only if IP > 4.0
    try:
        for side, team in (("awayPitchers", away_name), ("homePitchers", home_name)):
            starter_seen = False
            for p in box_data.get(side, []):
                if not p.get("personId"):
                    continue
                is_starter = not starter_seen
                starter_seen = True

                name = normalize_name((p.get("name") or "Unknown").strip())
                ip   = p.get("ip", "?")
                er   = p.get("er", "?")
                k    = p.get("k", "?")
                note = (p.get("note") or "").strip()

                # Compute IP as decimal for threshold check
                ip_dec = 0.0
                try:
                    parts  = str(ip).split('.')
                    ip_dec = int(parts[0]) + (int(parts[1]) / 3.0 if len(parts) > 1 else 0)
                except (ValueError, IndexError):
                    pass

                # Skip relievers who pitched ≤4.0 IP
                if not is_starter and ip_dec <= 4.0:
                    continue

                # Decision label (if any)
                decision_label = ""
                if note.startswith("(W"):
                    decision_label = " (W)"
                elif note.startswith("(L"):
                    decision_label = " (L)"
                elif note.startswith("(S"):
                    decision_label = " (S)"

                # Quality start: 6+ IP and ≤3 ER
                qs_flag = ""
                try:
                    if ip_dec >= 6.0 and str(er).isdigit() and int(er) <= 3:
                        qs_flag = " [QS]"
                except (ValueError, IndexError):
                    pass

                role = "Starting Pitcher" if is_starter else "Relief Pitcher"
                lines.append(f"[{team}] {role} {name}{decision_label}{qs_flag}: {ip} IP, {er} ER, {k} K")
    except Exception:
        pass

    # Notable batters: HR, RBI (context-aware threshold), 3+ hits, triples, 2+ runs
    total_runs    = away_score + home_score
    rbi_threshold = 1 if total_runs <= 3 else 2
    try:
        for side, team in (("awayBatters", away_name), ("homeBatters", home_name)):
            for b in box_data.get(side, []):
                if not b.get("personId"):
                    continue
                name = normalize_name((b.get("name") or "").strip())
                if not name or name.lower() == "totals":
                    continue
                hr  = int(b.get("hr")  or 0)
                rbi = int(b.get("rbi") or 0)
                r   = int(b.get("r")   or 0)
                h   = int(b.get("h")   or 0)
                ab  = b.get("ab", "?")
                d   = int(b.get("doubles") or b.get("d") or 0)
                t   = int(b.get("triples") or b.get("t") or 0)
                sb  = int(b.get("sb")  or 0)
                bb  = int(b.get("bb")  or 0)

                if not (hr > 0 or rbi >= rbi_threshold or h >= 3 or t > 0 or r >= 2):
                    continue

                extras = []
                if hr > 0:               extras.append(f"{hr} HR")
                if d > 0:                extras.append(f"{d} 2B")
                if t > 0:                extras.append(f"{t} 3B")
                if rbi >= rbi_threshold: extras.append(f"{rbi} RBI")
                if r >= 2:               extras.append(f"{r} R")
                if bb > 0:               extras.append(f"{bb} BB")
                if sb > 0:               extras.append(f"{sb} SB")

                if extras:
                    lines.append(f"[{team}] Batter {name}: {h}/{ab}, {', '.join(extras)}")
                else:
                    lines.append(f"[{team}] Batter {name}: {h}/{ab} (multi-hit)")
    except Exception:
        pass

    return "\n".join(lines) if lines else "Detailed player stats unavailable."


def extract_display_stats(box_data, game):
    """Return structured pitcher decisions and top batter for card display."""
    w_full = (game.get('winning_pitcher') or '').strip()
    l_full = (game.get('losing_pitcher')  or '').strip()
    s_full = (game.get('save_pitcher')    or '').strip()

    pitchers = {
        'w': {'name': w_full, 'record': '', 'ip': '', 'er': '', 'k': '', 'qs': False},
        'l': {'name': l_full, 'record': '', 'ip': '', 'er': '', 'k': '', 'qs': False},
        's': {'name': s_full, 'record': '', 'ip': '', 'er': '', 'k': '', 'qs': False},
    }
    top_batter = None

    if not box_data:
        return pitchers, top_batter

    away_name = game['away_name']
    home_name = game['home_name']

    def last_name(full):
        return full.split()[-1].lower() if full else ''

    def box_matches(full_last, box_name):
        bn = _ascii(box_name)
        fl = _ascii(full_last)
        return bool(fl) and (fl in bn or bn.endswith(fl))

    w_last, l_last, s_last = last_name(w_full), last_name(l_full), last_name(s_full)

    for side in ('awayPitchers', 'homePitchers'):
        for p in box_data.get(side, []):
            if not p.get('personId'):
                continue
            name = normalize_name((p.get('name') or '').strip())
            note = (p.get('note') or '').strip()
            ip   = str(p.get('ip', '') or '')
            er   = str(p.get('er', '') or '')
            k    = str(p.get('k',  '') or '')

            qs = False
            try:
                parts  = ip.split('.')
                ip_dec = int(parts[0]) + (int(parts[1]) / 3.0 if len(parts) > 1 else 0)
                if ip_dec >= 6.0 and er.isdigit() and int(er) <= 3:
                    qs = True
            except (ValueError, IndexError):
                pass

            key = None
            if note.startswith('(W') and box_matches(w_last, name): key = 'w'
            elif note.startswith('(L') and box_matches(l_last, name): key = 'l'
            elif note.startswith('(S') and box_matches(s_last, name): key = 's'

            if key:
                m = re.search(r'\([WLS],\s*([\d\-]+)\)', note)
                pitchers[key].update({
                    'record': m.group(1) if m else '',
                    'ip': ip, 'er': er, 'k': k, 'qs': qs,
                })

    # Top batter: weighted HR > RBI > hits
    best_score = 0
    for side, team in (('awayBatters', away_name), ('homeBatters', home_name)):
        for b in box_data.get(side, []):
            if not b.get('personId'):
                continue
            name = normalize_name((b.get('name') or '').strip())
            if not name or name.lower() == 'totals':
                continue
            hr  = int(b.get('hr')  or 0)
            rbi = int(b.get('rbi') or 0)
            h   = int(b.get('h')   or 0)
            ab  = b.get('ab', '?')
            d   = int(b.get('doubles') or b.get('d') or 0)
            t   = int(b.get('triples') or b.get('t') or 0)
            sb  = int(b.get('sb')  or 0)
            if not (hr > 0 or rbi >= 2 or h >= 3):
                continue
            score = hr * 10 + rbi * 3 + h
            if score > best_score:
                best_score = score
                top_batter = {
                    'name': name, 'team': team,
                    'h': h, 'ab': ab, 'hr': hr, 'rbi': rbi,
                    'd': d, 't': t, 'sb': sb,
                    'exceptional': hr >= 2 or rbi >= 4,
                }

    # Collect last names of ALL QS-qualifying pitchers (including no-decision starters)
    qs_pitchers = set()
    for side in ('awayPitchers', 'homePitchers'):
        for p in box_data.get(side, []):
            if not p.get('personId'):
                continue
            ip = str(p.get('ip', '') or '')
            er = str(p.get('er', '') or '')
            try:
                parts  = ip.split('.')
                ip_dec = int(parts[0]) + (int(parts[1]) / 3.0 if len(parts) > 1 else 0)
                if ip_dec >= 6.0 and er.isdigit() and int(er) <= 3:
                    n = normalize_name((p.get('name') or '').strip())
                    if n:
                        qs_pitchers.add(_ascii(n.split()[-1]))
            except (ValueError, IndexError):
                pass

    return pitchers, top_batter, qs_pitchers


# ---------------------------------------------------------------------------
# Game context helpers for Groq
# ---------------------------------------------------------------------------

def is_walkoff(game):
    """True if the home team won in the bottom of the final inning."""
    return (
        game.get("inning_state") == "Bottom"
        and int(game["home_score"]) > int(game["away_score"])
    )


def build_game_context(game):
    """Return a comma-joined string of notable game circumstances."""
    away_score = int(game["away_score"])
    home_score = int(game["home_score"])
    run_diff   = abs(away_score - home_score)
    innings    = int(game.get("current_inning") or 9)

    flags = []
    if is_walkoff(game):
        flags.append(f"walk-off win for {game['home_name']}")
    if innings > 9:
        flags.append(f"went {innings} innings (extra innings)")
    if run_diff == 1 and not is_walkoff(game):
        flags.append("decided by 1 run")
    elif run_diff >= 8:
        flags.append(f"blowout ({run_diff}-run margin)")
    if away_score == 0 or home_score == 0:
        loser = game["away_name"] if away_score == 0 else game["home_name"]
        flags.append(f"shutout — {loser} held scoreless")

    return ", ".join(flags) if flags else ""


def get_tone_instruction(game):
    """Return a writing-tone hint based on the game's shape."""
    innings    = int(game.get("current_inning") or 9)
    run_diff   = abs(int(game["away_score"]) - int(game["home_score"]))
    away_score = int(game["away_score"])
    home_score = int(game["home_score"])

    if is_walkoff(game):
        return f"Write with excitement — {game['home_name']} won on a walk-off. Make sure to convey the drama."
    if innings > 9:
        return "Write with excitement — this was a dramatic extra-innings game."
    if run_diff == 1:
        return "Write with tension — this was a tight 1-run game."
    if run_diff >= 8:
        return "Keep it brief — this was a blowout. A dry or wry observation about the margin or the losing side is welcome and encouraged."
    if away_score == 0 or home_score == 0:
        return "Highlight the dominant pitching performance — this was a shutout."
    return "Write a natural, engaging recap."


# ---------------------------------------------------------------------------
# Team trend helper
# ---------------------------------------------------------------------------

def fetch_team_trends(date_str):
    """Return {team_id: trend_str} for hot (7+ wins) or cold (3- wins) teams over last 10 games."""
    target_dt = datetime.strptime(date_str, "%m/%d/%Y")
    start_str = (target_dt - timedelta(days=14)).strftime("%m/%d/%Y")
    try:
        all_games = statsapi.schedule(start_date=start_str, end_date=date_str)
    except Exception as e:
        print(f"  [warn] Could not fetch team trends: {e}")
        return {}

    completed = [g for g in all_games
                 if g.get('status') in ('Final', 'Game Over', 'Completed Early')]

    team_results = {}
    for g in completed:
        away_id = g['away_id']
        home_id = g['home_id']
        ascore  = int(g['away_score'])
        hscore  = int(g['home_score'])
        for tid, won in ((away_id, ascore > hscore), (home_id, hscore > ascore)):
            team_results.setdefault(tid, []).append(won)

    trends = {}
    for team_id, results in team_results.items():
        last_10 = results[-10:]
        if len(last_10) < 10:
            continue
        wins = sum(last_10)
        if wins >= 7 or wins <= 3:
            trends[team_id] = f"{wins}-{10 - wins} over their last 10"

    return trends


# ---------------------------------------------------------------------------
# Key moments (walk-off, big inning, comeback, go-ahead) for prompt accuracy
# ---------------------------------------------------------------------------

def _ordinal(n):
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{({1:'st',2:'nd',3:'rd'}).get(n % 10, 'th')}"


def get_key_moments(game_pk, away_name, home_name, final_away, final_home):
    """Return (moments_str, linescore) — fetches game data once for both."""
    try:
        data         = statsapi.get('game', {'gamePk': game_pk})
        all_plays    = data['liveData']['plays']['allPlays']
        scoring_idxs = data['liveData']['plays']['scoringPlays']
    except Exception:
        return '', []

    # Extract linescore from the same API response
    linescore = []
    try:
        for inn in data['liveData']['linescore']['innings']:
            linescore.append({
                'inning': inn.get('num', 0),
                'away':   inn.get('away', {}).get('runs', 0),
                'home':   inn.get('home', {}).get('runs', 0),
            })
    except Exception:
        pass

    if not scoring_idxs or not all_plays:
        return '', linescore

    # Build enriched list of scoring plays
    plays = []
    for pos, idx in enumerate(scoring_idxs):
        try:
            play      = all_plays[idx]
            away_now  = int(play['result'].get('awayScore', 0))
            home_now  = int(play['result'].get('homeScore', 0))
            if idx > 0:
                prev      = all_plays[idx - 1]
                away_prev = int(prev['result'].get('awayScore', 0))
                home_prev = int(prev['result'].get('homeScore', 0))
            else:
                away_prev = home_prev = 0
            plays.append({
                'inning':    play['about']['inning'],
                'half':      play['about']['halfInning'],
                'batter':    play['matchup']['batter']['fullName'],
                'event':     play['result'].get('event', ''),
                'rbi':       int(play['result'].get('rbi', 0)),
                'away_now':  away_now,  'home_now':  home_now,
                'away_prev': away_prev, 'home_prev': home_prev,
                'is_last':   pos == len(scoring_idxs) - 1,
            })
        except Exception:
            continue

    if not plays:
        return ''

    moments     = []
    winner_away = final_away > final_home

    # ── 1. Walk-off ──────────────────────────────────────────────────────────
    last    = plays[-1]
    walkoff = (last['is_last'] and last['half'] == 'bottom'
               and final_home > final_away
               and last['home_now'] > last['away_now']
               and last['home_prev'] <= last['away_prev'])
    if walkoff:
        rbi_str   = f"{last['rbi']}-run " if last['rbi'] > 1 else ""
        inn_label = f"bottom {_ordinal(last['inning'])}" + (" (extra innings)" if last['inning'] > 9 else "")
        moments.append(f"Walk-off: {last['batter']} — {rbi_str}{last['event']} ({inn_label})")

    # ── 2. Big inning (5+ runs in a single half-inning) ──────────────────────
    inning_runs = {}
    for p in plays:
        key  = (p['inning'], p['half'])
        runs = (p['home_now'] - p['home_prev']) if p['half'] == 'bottom' else (p['away_now'] - p['away_prev'])
        inning_runs[key] = inning_runs.get(key, 0) + runs
    for (inning, half), runs in sorted(inning_runs.items()):
        if runs >= 5:
            team      = home_name if half == 'bottom' else away_name
            half_name = 'bottom' if half == 'bottom' else 'top'
            moments.append(f"Big inning: {team} scored {runs} in the {half_name} of the {_ordinal(inning)}")

    # ── 3. Comeback (winning team trailing by 2+ after 6th) ──────────────────
    away_6 = home_6 = 0
    for p in plays:
        if p['inning'] <= 6:
            away_6, home_6 = p['away_now'], p['home_now']

    deficit     = (home_6 - away_6) if winner_away else (away_6 - home_6)
    is_comeback = deficit >= 2

    if is_comeback:
        winner_name    = away_name if winner_away else home_name
        trailing_score = f"{away_6}-{home_6}" if winner_away else f"{home_6}-{away_6}"
        if walkoff:
            # Go-ahead already captured by walk-off — just add deficit context
            moments.append(f"Comeback context: {winner_name} trailed {trailing_score} after 6 innings")
        else:
            # Find final go-ahead play (last time winner went from not-leading → leading)
            goahead = None
            for p in plays:
                if winner_away:
                    if p['away_prev'] <= p['home_prev'] and p['away_now'] > p['home_now']:
                        goahead = p
                else:
                    if p['home_prev'] <= p['away_prev'] and p['home_now'] > p['away_now']:
                        goahead = p
            if goahead:
                rbi_str   = f"{goahead['rbi']}-run " if goahead['rbi'] > 1 else ""
                half_name = 'bottom' if goahead['half'] == 'bottom' else 'top'
                moments.append(
                    f"Comeback: {winner_name} trailed {trailing_score} after 6 → "
                    f"{goahead['batter']} go-ahead {rbi_str}{goahead['event']} "
                    f"({half_name} {_ordinal(goahead['inning'])})"
                )

    # ── 4. Go-ahead (final lead change in 7th+, only if no walk-off or comeback) ──
    if not walkoff and not is_comeback:
        goahead = None
        for p in plays:
            if p['inning'] < 7:
                continue
            if winner_away:
                if p['away_prev'] <= p['home_prev'] and p['away_now'] > p['home_now']:
                    goahead = p
            else:
                if p['home_prev'] <= p['away_prev'] and p['home_now'] > p['away_now']:
                    goahead = p
        if goahead:
            rbi_str   = f"{goahead['rbi']}-run " if goahead['rbi'] > 1 else ""
            team      = away_name if goahead['half'] == 'top' else home_name
            half_name = 'top' if goahead['half'] == 'top' else 'bottom'
            inn_label = (f"{half_name} {_ordinal(goahead['inning'])}"
                         + (" (extra innings)" if goahead['inning'] > 9 else ""))
            moments.append(
                f"Go-ahead: {goahead['batter']} ({team}) — {rbi_str}{goahead['event']} ({inn_label})"
            )

    return '\n'.join(moments), linescore


# ---------------------------------------------------------------------------
# Groq summarisation
# ---------------------------------------------------------------------------

def summarize_game(client, game, box_data, token_totals, trends=None, key_moments='', retries=3):
    away         = game["away_name"]
    home         = game["home_name"]
    away_score   = game["away_score"]
    home_score   = game["home_score"]
    winner       = away if int(away_score) > int(home_score) else home
    player_notes = extract_player_notes(box_data, away, home, int(away_score), int(home_score))
    game_context = build_game_context(game)
    tone         = get_tone_instruction(game)
    context_line = f"Game context: {game_context}\n" if game_context else ""

    # W/L/S pitcher names passed as context so RECAP can reference them naturally
    dec_parts = []
    for label, key in (('W', 'winning_pitcher'), ('L', 'losing_pitcher'), ('S', 'save_pitcher')):
        name = (game.get(key) or '').strip()
        if name:
            dec_parts.append(f"{label}: {name}")
    decisions_line = f"Pitcher decisions: {', '.join(dec_parts)}\n" if dec_parts else ""

    # Hot/cold team trends (only injected when a team is notably hot or cold)
    trend_parts = []
    for team_name, team_id in ((away, game.get('away_id')), (home, game.get('home_id'))):
        t = (trends or {}).get(team_id)
        if t:
            trend_parts.append(f"- {team_name}: {t}")
    trends_line = "Team trends (last 10 games):\n" + "\n".join(trend_parts) + "\n" if trend_parts else ""

    moments_line = f"Key moments:\n{key_moments}\n" if key_moments else ""

    prompt = f"""You are an MLB beat writer with a distinct voice: write like a knowledgeable friend who watches every game — direct, conversational, and comfortable with baseball idiom. On blowouts or when a team is visibly slumping, a dry or wry observation is welcome and encouraged. Otherwise keep it warm and engaging.

Write a recap using ONLY the information provided below.

RULES — follow exactly:
- KEY PLAYERS bullets: ONLY use players whose names appear in the Stats section. Do not add others.
- Always include a KEY PLAYERS bullet for EACH player labelled "Starting Pitcher" in the Stats section, regardless of how good or bad their outing was.
- For batters, only include players who made a meaningful impact: HR, 2+ RBI (or 1 RBI in a low-scoring game), 3+ hits, a triple, or 2+ runs scored. A single double or a 1-hit game with no other production does not qualify.
- Aim for no more than 5 KEY PLAYERS bullets total — prioritise the players who most decided the game.
- RECAP narrative: you may also reference pitcher decisions by name (from Pitcher decisions above) if it meaningfully adds context — but only when it naturally fits. Do not force it.
- If team trend data is shown, you may reference it naturally in the RECAP — but only when it genuinely adds context. Do not force it.
- If Key moments are listed, you may reference them naturally in the RECAP only — do not let them influence KEY PLAYERS in any way. They are factual (batter, event, inning, RBI count). Do not embellish beyond what is stated.
- NEVER use the words "walk-off" or "walk off" unless a walk-off is listed in Key moments. NEVER reference a specific inning, "go-ahead", or "extra innings" for any individual play unless it appears in Key moments. You do not know when in the game individual events happened.
- Each stat line is labelled [TeamName]. Only attribute a player to the team shown in their label — never mix players across teams.
- Use player names exactly as written (do not guess first names).
- Do not say "not in the stats" or similar. Write naturally about what happened.
- If stats are limited, write a shorter recap — do not pad with invented details.
- Do NOT add [QS] or any quality start notation to your output — omit it entirely.
- {tone}

Game: {away} {away_score}, {home} {home_score} — {winner} win
{context_line}{decisions_line}{trends_line}{moments_line}Stats:
{player_notes}

Output format (no extra commentary):
RECAP: [2-3 sentences on how the game went]
KEY PLAYERS:
• [name] ([team]) — [stat line]

Stat line format rules — follow exactly, no deviations:
- Batters: H-AB[, HR][, 2B][, 3B][, RBI][, R][, BB][, SB] — use only stats present, in that order, no extra words
- Pitchers: IP IP, ER ER, K K — use only stats present, in that order, no extra words
- Never use verbs like "hit", "went", "recorded" in the stat line — numbers only
- No commentary or phrases after the stat line"""

    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=380,
                temperature=0.65,
            )
            usage = resp.usage
            token_totals['prompt']     += usage.prompt_tokens
            token_totals['completion'] += usage.completion_tokens
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt < retries - 1:
                print(f"  [warn] Groq error ({e}), retrying in 3s...")
                time.sleep(3)
            else:
                hi = max(int(away_score), int(home_score))
                lo = min(int(away_score), int(home_score))
                loser = away if winner == home else home
                return (f"RECAP: {winner} defeated {loser} {hi}–{lo}.\n"
                        "KEY PLAYERS:\n• Stats unavailable for this game.")


# ---------------------------------------------------------------------------
# Series context + milestone helpers
# ---------------------------------------------------------------------------

def build_series_contexts(games, date_str):
    """Return {game_id: context_str} for games that are part of a multi-game series."""
    target_dt = datetime.strptime(date_str, "%m/%d/%Y")
    contexts  = {}

    try:
        raw = statsapi.get('schedule', {'date': date_str, 'sportId': 1})
        raw_info = {}
        for d in raw.get('dates', []):
            for g in d.get('games', []):
                raw_info[g['gamePk']] = {
                    'num':   g.get('seriesGameNumber', 1),
                    'total': g.get('gamesInSeries',    1),
                }
    except Exception as e:
        print(f"  [warn] Series context fetch failed: {e}")
        return contexts

    schedule_cache = {}

    def cached_schedule(dt_str):
        if dt_str not in schedule_cache:
            try:
                schedule_cache[dt_str] = statsapi.schedule(date=dt_str)
            except Exception:
                schedule_cache[dt_str] = []
        return schedule_cache[dt_str]

    for game in games:
        pk    = game['game_id']
        info  = raw_info.get(pk, {'num': 1, 'total': 1})
        g_num = info['num']
        total = info['total']

        if total <= 1:
            continue

        away_id    = game['away_id']
        home_id    = game['home_id']
        away_short = game['away_name'].split()[-1]
        home_short = game['home_name'].split()[-1]
        away_wins  = home_wins = 0

        if g_num > 1:
            for days_back in range(1, g_num + 1):
                check_str   = (target_dt - timedelta(days=days_back)).strftime("%m/%d/%Y")
                found_match = False
                for pg in cached_schedule(check_str):
                    if pg.get('status') not in ('Final', 'Game Over', 'Completed Early'):
                        continue
                    if {pg['away_id'], pg['home_id']} == {away_id, home_id}:
                        found_match = True
                        w_id = pg['away_id'] if int(pg['away_score']) > int(pg['home_score']) else pg['home_id']
                        if w_id == away_id:
                            away_wins += 1
                        else:
                            home_wins += 1
                if not found_match and days_back > 1:
                    break

        # Include this game's result in the series record
        curr_away = int(game.get('away_score', 0))
        curr_home = int(game.get('home_score', 0))
        if curr_away > curr_home:
            away_wins += 1
        elif curr_home > curr_away:
            home_wins += 1

        if away_wins == 0 and home_wins == 0:
            ctx = f"Game {g_num} of {total}"
        elif away_wins > home_wins:
            ctx = f"Game {g_num} of {total} · {away_short} lead series {away_wins}-{home_wins}"
        elif home_wins > away_wins:
            ctx = f"Game {g_num} of {total} · {home_short} lead series {home_wins}-{away_wins}"
        else:
            ctx = f"Game {g_num} of {total} · Series tied {away_wins}-{home_wins}"

        contexts[pk] = ctx

    return contexts


def detect_milestones(box_data):
    """Detect no-hitter and batting cycle from box score data."""
    result = {'game_flags': [], 'player_flags': []}
    if not box_data:
        return result

    # No-hitter: check if either team's batter totals row shows H = 0
    for batter_side, pitcher_side in (
        ('awayBatters', 'homePitchers'),
        ('homeBatters', 'awayPitchers'),
    ):
        for b in box_data.get(batter_side, []):
            if (b.get('name') or '').lower() != 'totals':
                continue
            if int(b.get('h') or 1) == 0:
                pitchers = [p for p in box_data.get(pitcher_side, []) if p.get('personId')]
                if len(pitchers) == 1:
                    pname = normalize_name((pitchers[0].get('name') or '').strip())
                    result['game_flags'].append(('NO-HITTER', f"No-Hitter — {pname}"))
                else:
                    result['game_flags'].append(('NO-HITTER', 'Combined No-Hitter'))
            break

    # Cycle: a batter with HR≥1, 2B≥1, 3B≥1, and at least one single
    for side in ('awayBatters', 'homeBatters'):
        for b in box_data.get(side, []):
            if not b.get('personId'):
                continue
            name = normalize_name((b.get('name') or '').strip())
            if not name or name.lower() == 'totals':
                continue
            h  = int(b.get('h')  or 0)
            hr = int(b.get('hr') or 0)
            d  = int(b.get('doubles') or b.get('d') or 0)
            t  = int(b.get('triples') or b.get('t') or 0)
            if hr >= 1 and d >= 1 and t >= 1 and (h - hr - d - t) >= 1:
                result['player_flags'].append(('CYCLE', f"Cycle — {name}"))

    return result


# ---------------------------------------------------------------------------
# HTML email builder
# ---------------------------------------------------------------------------

def streak_badge(code, margin='8px'):
    """Return a streak badge span, or '' if the streak is under 2."""
    if not code:
        return ''
    s = str(code).upper()
    try:
        num = int(s[1:])
    except (ValueError, IndexError):
        return ''
    if num < 2:
        return ''
    cls = 'sw' if s.startswith('W') else 'sl'
    return f'<span class="{cls}" style="margin-left:{margin};">{code}</span>'


def render_summary_html(summary, pitchers, top_batter=None, qs_pitchers=None):
    """Escape summary text, inject QS badges for qualifying pitchers, bold top batter."""
    # Build unified set of accent-stripped last names that deserve a QS badge
    qs_last_names = set(qs_pitchers or set())
    for key in ('w', 'l', 's'):
        p = pitchers.get(key, {})
        if p.get('qs') and p.get('name'):
            qs_last_names.add(_ascii(p['name'].split()[-1]))

    if qs_last_names:
        lines = summary.split('\n')
        for i, line in enumerate(lines):
            if '•' in line and '[QS]' not in line:
                line_ascii = _ascii(line)
                for last in qs_last_names:
                    if last in line_ascii:
                        lines[i] = lines[i].rstrip() + ' [QS]'
                        break
        summary = '\n'.join(lines)

    # Bold the entire bullet for the top batter in KEY PLAYERS
    if top_batter and top_batter.get('name'):
        name = top_batter['name']
        batter_key = name.split(',')[0].strip() if ',' in name else name.split()[-1]
        lines = summary.split('\n')
        for i, line in enumerate(lines):
            if '•' in line and batter_key in line and '[BOLD]' not in line:
                lines[i] = f'[BOLD]{line}[/BOLD]'
                break
        summary = '\n'.join(lines)

    qs_badge = '<span class="qs">QS</span>'
    escaped = (html.escape(summary)
               .replace('[QS]', qs_badge)
               .replace('[BOLD]', '<strong>')
               .replace('[/BOLD]', '</strong>')
               .replace('\n', '<br>'))
    return escaped


def render_linescore_html(linescore, away_name, home_name):
    if not linescore:
        return ''
    away_abbr  = TEAM_ABBREVS.get(away_name, away_name[:3].upper())
    home_abbr  = TEAM_ABBREVS.get(home_name, home_name[:3].upper())
    total_away = sum(inn['away'] for inn in linescore)
    total_home = sum(inn['home'] for inn in linescore)

    def run_cell(runs):
        return f'<td class="l1">{runs}</td>' if runs > 0 else f'<td class="l0">0</td>'

    inning_hdrs = ''.join(f'<td class="lh">{inn["inning"]}</td>' for inn in linescore)
    away_cells  = ''.join(run_cell(inn['away']) for inn in linescore)
    home_cells  = ''.join(run_cell(inn['home']) for inn in linescore)

    return (
        f'<div class="lw"><table class="lt" cellpadding="0" cellspacing="0">'
        f'<tr><td class="ll" style="color:#94a3b8;"></td>{inning_hdrs}<td class="lrh">R</td></tr>'
        f'<tr><td class="ll">{away_abbr}</td>{away_cells}<td class="lr">{total_away}</td></tr>'
        f'<tr><td class="ll">{home_abbr}</td>{home_cells}<td class="lr">{total_home}</td></tr>'
        f'</table></div>'
    )


def render_game_card(gs):
    away, home = gs['matchup'].split(' @ ')
    away_rec   = gs.get('away_record', '')
    home_rec   = gs.get('home_record', '')
    winner     = gs.get('winner', '')
    away_score = gs.get('away_score_val', '')
    home_score = gs.get('home_score_val', '')
    away_won   = winner == away

    # Team color left border (idea 5)
    team_color  = TEAM_COLORS.get(winner, '#1d4ed8')

    away_cls   = 'tw' if away_won else 'tl'
    home_cls   = 'tw' if not away_won else 'tl'
    away_nick  = TEAM_NICKNAMES.get(away, away.split()[-1])
    home_nick  = TEAM_NICKNAMES.get(home, home.split()[-1])
    away_label = f"{away_nick} ({away_rec})" if away_rec else away_nick
    home_label = f"{home_nick} ({home_rec})" if home_rec else home_nick

    away_opacity = '1.0' if away_won else '0.4'
    home_opacity = '1.0' if not away_won else '0.4'
    away_logo = (f'<img src="https://www.mlbstatic.com/team-logos/{gs.get("away_id","")}.svg" '
                 f'class="lg" alt="{away_nick}" style="opacity:{away_opacity};">')
    home_logo = (f'<img src="https://www.mlbstatic.com/team-logos/{gs.get("home_id","")}.svg" '
                 f'class="lg" alt="{home_nick}" style="opacity:{home_opacity};">')

    # Streak badges next to each team name
    away_streak_badge = streak_badge(gs.get('winner_streak') if away_won else gs.get('loser_streak'), margin='6px')
    home_streak_badge = streak_badge(gs.get('winner_streak') if not away_won else gs.get('loser_streak'), margin='6px')

    game_badges = ''
    if gs.get('walkoff'):
        game_badges += '<span class="bdg bw">WALK-OFF</span>'
    if gs.get('extra_innings'):
        game_badges += f'<span class="bdg bi">{gs["innings"]} INN</span>'
    if gs.get('game_num'):
        game_badges += f'<span class="bdg bg">{gs["game_num"]}</span>'
    for flag_type, flag_text in gs.get('milestones', {}).get('game_flags', []):
        game_badges += f'<span class="bdg bm">{flag_text}</span>'
    for flag_type, flag_text in gs.get('milestones', {}).get('player_flags', []):
        game_badges += f'<span class="bdg bp">{flag_text}</span>'

    away_pip_color = '#4ade80' if away_won else '#f87171'
    home_pip_color = '#4ade80' if not away_won else '#f87171'
    score_pill = (
        f'<span class="pl">'
        f'<span class="pn" style="color:{away_pip_color};">{away_score}</span>'
        f'<span class="ps">–</span>'
        f'<span class="pn" style="color:{home_pip_color};">{home_score}</span>'
        f'</span>'
    )

    # Pitcher decisions row (idea 4)
    pitchers   = gs.get('pitchers', {})
    p_parts    = []
    for key, label in (('w', 'W'), ('l', 'L'), ('s', 'S')):
        p = pitchers.get(key, {})
        if not p.get('name'):
            continue
        last = p['name'].split()[-1]
        rec  = f" ({p['record']})" if p.get('record') else ''
        p_parts.append(f"{label}: {last}{rec}")
    pitcher_row = (' &nbsp;&middot;&nbsp; '.join(p_parts)) if p_parts else ''

    top_batter    = gs.get('top_batter')
    linescore_html = render_linescore_html(gs.get('linescore', []), away, home)

    meta_section = ''
    if pitcher_row:
        meta_section = f'<div class="pr">{pitcher_row}</div>'
    if gs.get('series_context'):
        meta_section += f'<div class="sc">{gs["series_context"]}</div>'

    return (
        f'<div class="card" style="border-left:4px solid {team_color};">'
        f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td class="mu">'
        f'{away_logo}<span class="{away_cls}">{away_label}</span>{away_streak_badge}'
        f'<span class="ts">@</span>'
        f'{home_logo}<span class="{home_cls}">{home_label}</span>{home_streak_badge}{game_badges}'
        f'</td>'
        f'<td align="right" style="vertical-align:middle;padding-left:12px;">{score_pill}</td>'
        f'</tr></table>'
        f'{meta_section}{linescore_html}'
        f'<div class="rc">{render_summary_html(gs["summary"], pitchers, top_batter, gs.get("qs_pitchers", set()))}</div>'
        f'</div>'
    )


def build_html_email(date_display, game_summaries):
    if not game_summaries:
        body_content = """<div style="text-align:center;padding:48px 0;color:#94a3b8;
                                      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
                            No completed MLB games yesterday.
                          </div>"""
        count_line = "Off day"
    else:
        # Sort by excitement: extra innings first, then by run differential ascending
        sorted_summaries = sorted(
            game_summaries,
            key=lambda gs: (0 if gs.get('extra_innings') else 1, gs.get('run_diff', 99))
        )

        # "Game of the Night" banner for extra innings or 1-run games
        top      = sorted_summaries[0]
        run_diff = top.get('run_diff', 99)
        t_away, t_home = top['matchup'].split(' @ ')
        t_ascore = top.get('away_score_val', '')
        t_hscore = top.get('home_score_val', '')

        if top.get('extra_innings'):
            banner_label = "Extra Innings Thriller"
            banner_desc  = f"{t_away} {t_ascore} – {t_home} {t_hscore} ({top.get('innings', '?')} innings)"
        elif run_diff == 1:
            banner_label = "Game of the Night"
            banner_desc  = f"{t_away} {t_ascore} – {t_home} {t_hscore}"
        else:
            banner_label = banner_desc = None

        banner = ""
        if banner_label:
            banner = (f'<div class="bn"><div class="bn-l">&#11088; {banner_label}</div>'
                      f'<div class="bn-d">{banner_desc}</div></div>')

        body_content = banner + "".join(render_game_card(gs) for gs in sorted_summaries)
        count_line   = f"{len(game_summaries)} game{'s' if len(game_summaries) != 1 else ''} played"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
body{{margin:0;padding:0;background:#f1f5f9}}.wrap{{max-width:660px;margin:0 auto;padding:28px 16px 48px}}.hdr{{background:linear-gradient(135deg,#1e3a5f 0%,#1e40af 100%);border-radius:12px 12px 0 0;padding:30px 32px;text-align:center}}.hdr h1{{margin:0 0 4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:22px;font-weight:800;color:#fff;letter-spacing:-.3px}}.hdr p{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;color:#93c5fd}}.body{{background:#f8fafc;padding:24px 24px 8px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px}}.ft{{text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:11px;color:#94a3b8;margin-top:20px}}.bn{{background:linear-gradient(135deg,#0f3460 0%,#1e40af 100%);border-radius:8px;padding:14px 20px;margin-bottom:20px}}.bn-l{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:10px;font-weight:700;color:#93c5fd;letter-spacing:.1em;text-transform:uppercase;margin-bottom:3px}}.bn-d{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;font-weight:600;color:#fff}}.card{{background:#fff;border-top:1px solid #e2e8f0;border-right:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;border-radius:10px;padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}.mu{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;vertical-align:middle}}.tw{{font-weight:700;color:#1e293b}}.tl{{font-weight:400;color:#94a3b8}}.ts{{color:#cbd5e1;font-weight:300;margin:0 6px}}.lg{{width:22px;height:22px;vertical-align:middle;margin-right:5px}}.pl{{display:inline-block;background:#0f172a;border-radius:6px;padding:5px 14px;white-space:nowrap}}.pn{{font-family:'Courier New',monospace;font-size:19px;font-weight:800}}.ps{{font-family:'Courier New',monospace;font-size:14px;color:#334155;margin:0 6px}}.pr{{font-size:13.5px;color:#374151;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:8px 0 0;margin-top:2px;border-top:1px solid #f1f5f9}}.sc{{font-size:11px;color:#94a3b8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:3px 0 2px}}.rc{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13.5px;color:#374151;line-height:1.7;margin-top:12px}}.bdg{{font-size:10px;font-family:monospace;border-radius:3px;padding:1px 6px}}.bw{{background:#fef9c3;color:#854d0e;border:1px solid #fde047;margin-left:8px}}.bi{{background:#fef9c3;color:#854d0e;border:1px solid #fde047;margin-left:6px}}.bg{{background:#f1f5f9;color:#64748b;border:1px solid #cbd5e1;margin-left:6px}}.bm{{background:#f5f3ff;color:#6d28d9;border:1px solid #c4b5fd;margin-left:6px}}.bp{{background:#fdf4ff;color:#9333ea;border:1px solid #e9d5ff;margin-left:6px}}.sw{{font-size:10px;font-family:monospace;background:#f0fdf4;color:#16a34a;border:1px solid #86efac;border-radius:3px;padding:1px 6px}}.sl{{font-size:10px;font-family:monospace;background:#fef2f2;color:#dc2626;border:1px solid #fca5a5;border-radius:3px;padding:1px 6px}}.qs{{font-size:10px;background:#dbeafe;color:#1d4ed8;border-radius:2px;padding:0 4px;margin-left:3px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}.lw{{margin-top:8px;padding-top:6px;border-top:1px solid #f1f5f9;overflow-x:auto}}.lt{{border-collapse:collapse}}.ll{{padding:1px 8px 1px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:11px;font-weight:600;color:#64748b;white-space:nowrap}}.lh{{padding:1px 5px;text-align:center;font-family:'Courier New',monospace;font-size:11px;color:#94a3b8}}.l0{{padding:1px 5px;text-align:center;font-family:'Courier New',monospace;font-size:11px;color:#cbd5e1}}.l1{{padding:1px 5px;text-align:center;font-family:'Courier New',monospace;font-size:11px;color:#1e293b;font-weight:600}}.lr{{padding:1px 5px 1px 8px;text-align:center;font-family:'Courier New',monospace;font-size:11px;color:#1e293b;font-weight:700;border-left:1px solid #e2e8f0}}.lrh{{padding:1px 5px 1px 8px;text-align:center;font-family:'Courier New',monospace;font-size:11px;color:#94a3b8;border-left:1px solid #e2e8f0}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hdr">
      <div style="font-size:36px;line-height:1;margin-bottom:8px;">&#9918;</div>
      <h1>Daily Baseball Digest</h1>
      <p>{date_display} &nbsp;&#183;&nbsp; {count_line}</p>
    </div>
    <div class="body">{body_content}</div>
    <p class="ft">Automated digest &nbsp;&#183;&nbsp; MLB Stats API + Groq AI &nbsp;&#183;&nbsp; Free tier</p>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Email sender + local save
# ---------------------------------------------------------------------------

def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())


def save_html_locally(html_body, date_str, date_display):
    out_dir  = get_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    fname    = f"digest_{date_str.replace('/', '-')}.html"
    filepath = os.path.join(out_dir, fname)
    try:
        with open(filepath, 'wb') as f:
            f.write(html_body.encode('utf-8'))
        print(f"  HTML saved locally -> {filepath}")
    except Exception as e:
        print(f"  [warn] Could not save HTML: {e}")
        return
    generate_index_html(out_dir, date_display)
    push_to_github(out_dir, fname, date_str)


def push_to_github(digests_dir, fname, date_str):
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return  # workflow handles git commit/push
    try:
        subprocess.run(["git", "-C", digests_dir, "add", fname, "index.html"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", digests_dir, "commit", "-m", f"digest {date_str}"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", digests_dir, "push", "origin", "main"],
                       check=True, capture_output=True)
        print(f"  Published -> https://rohankalani1.github.io/mlb-digest/{fname}")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors='replace').strip() if e.stderr else str(e)
        print(f"  [warn] Could not push to GitHub: {err}")


def generate_index_html(out_dir, date_display):
    """Regenerate index.html — direct embed, prev/next nav, 2-col desktop layout."""
    import glob as _glob
    import re as _re

    digest_files = sorted(
        [os.path.basename(f) for f in _glob.glob(os.path.join(out_dir, 'digest_*.html'))],
        reverse=True,
    )
    if not digest_files:
        return

    # Build (fname, short "Jun 24", full "June 24, 2026") for each digest
    nav_items = []
    for fname in digest_files:
        try:
            dt    = datetime.strptime(fname.replace('digest_', '').replace('.html', ''), '%m-%d-%Y')
            full  = dt.strftime('%B %d, %Y')
            short = dt.strftime('%b') + ' ' + str(dt.day)
        except ValueError:
            full = short = fname
        nav_items.append((fname, short, full))

    latest_fname, _, latest_full = nav_items[0]

    # Extract the digest's CSS and <body> content from the latest file
    digest_css  = ''
    digest_body = ''
    try:
        with open(os.path.join(out_dir, latest_fname), 'r', encoding='utf-8') as fh:
            raw = fh.read()
        m = _re.search(r'<style>([\s\S]*?)</style>', raw)
        if m:
            digest_css = m.group(1).strip()
        m2 = _re.search(r'<body>([\s\S]*?)</body>', raw)
        if m2:
            digest_body = m2.group(1).strip()
    except Exception:
        pass

    # JS arrays (safe: filenames and date strings contain no curly braces)
    js_files  = 'const files='  + '[' + ','.join(f'"{f}"' for f, _, _ in nav_items) + ']'
    js_shorts = 'const shorts=' + '[' + ','.join(f'"{s}"' for _, s, _ in nav_items) + ']'
    js_fulls  = 'const fulls='  + '[' + ','.join(f'"{l}"' for _, _, l in nav_items) + ']'

    # Build page via string concatenation so digest CSS/HTML never enters an f-string
    # (CSS and HTML both contain { } which would break f-string parsing)
    site_css = """\
*{margin:0;padding:0;box-sizing:border-box}
html,body{min-height:100%;background:linear-gradient(180deg,#dde9ff 0%,#f1f5f9 28%);font-family:'Inter',-apple-system,sans-serif}
.site-nav{height:56px;background:linear-gradient(135deg,#1e3a5f 0%,#1e40af 100%);display:flex;align-items:center;justify-content:space-between;padding:0 20px;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.3);position:sticky;top:0;z-index:100}
.brand{display:flex;align-items:center;gap:9px;flex-shrink:0}
.brand-icon{font-size:22px;line-height:1}
.brand-title{font-size:15px;font-weight:800;color:#fff;letter-spacing:-.3px}
.brand-sub{font-size:10px;color:#93c5fd;margin-top:1px}
.day-nav{display:flex;align-items:center;gap:8px}
.nav-btn{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);color:#fff;padding:5px 12px;border-radius:6px;font-size:12px;font-weight:600;font-family:inherit;cursor:pointer;transition:background .15s;white-space:nowrap}
.nav-btn:hover:not(:disabled){background:rgba(255,255,255,.25)}
.nav-btn:disabled{opacity:.3;cursor:default}
.nav-date{font-size:13px;font-weight:700;color:#fff;min-width:120px;text-align:center;white-space:nowrap}
.theme-btn{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);color:#fff;width:34px;height:34px;border-radius:6px;font-size:16px;cursor:pointer;flex-shrink:0;line-height:1;padding:0}
.theme-btn:hover{background:rgba(255,255,255,.25)}
#dw{transition:opacity .2s}
"""

    override_css = """\
/* larger score pill */
#dw .pl{padding:7px 18px}
#dw .pn{font-size:22px}
/* card hover lift */
#dw .card{transition:transform .15s,box-shadow .15s;cursor:default}
#dw .card:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.1)}
/* hide the digest's own header/footer — replaced by site nav */
#dw .hdr,#dw .ft{display:none}
#dw .body{border-top:1px solid #e2e8f0;border-radius:12px}
#dw .wrap{padding-top:24px}
/* key players section header */
.kp-hdr{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin:10px 0 4px;padding-top:10px;border-top:1px solid #e2e8f0}
/* 2-column grid on wide screens */
@media(min-width:1100px){
  #dw .wrap{max-width:1240px;padding:24px 32px 48px}
  #dw .body{display:grid;grid-template-columns:1fr 1fr;column-gap:20px;background:transparent;border:none;padding:0;border-radius:0}
  #dw .bn{grid-column:1/-1}
}
/* dark mode */
[data-theme="dark"] body{background:#0f172a}
[data-theme="dark"] #dw .card{background:#1e293b;border-color:#334155 !important}
[data-theme="dark"] #dw .tw{color:#f1f5f9}
[data-theme="dark"] #dw .rc{color:#cbd5e1}
[data-theme="dark"] #dw .pr{color:#94a3b8;border-top-color:#334155}
[data-theme="dark"] #dw .sc{color:#64748b}
[data-theme="dark"] #dw .body{background:transparent;border-color:#334155}
[data-theme="dark"] #dw .wrap{background:transparent}
[data-theme="dark"] #dw .ts{color:#475569}
[data-theme="dark"] #dw .ps{color:#475569}
[data-theme="dark"] #dw .bn{background:linear-gradient(135deg,#0f2038 0%,#1a2f6b 100%)}
[data-theme="dark"] #dw .l0{color:#475569}
[data-theme="dark"] #dw .l1{color:#e2e8f0}
[data-theme="dark"] #dw .lr{color:#f1f5f9;border-left-color:#334155}
[data-theme="dark"] #dw .lrh{color:#64748b;border-left-color:#334155}
[data-theme="dark"] #dw .ll{color:#94a3b8}
[data-theme="dark"] #dw .lh{color:#64748b}
[data-theme="dark"] #dw .lw{border-top-color:#334155}
[data-theme="dark"] .kp-hdr{color:#64748b;border-top-color:#334155}
@media(max-width:500px){.brand-sub{display:none}}
"""

    js_core = """\
function toggleTheme(){
  var dark=document.documentElement.getAttribute("data-theme")==="dark";
  var t=dark?"light":"dark";
  document.documentElement.setAttribute("data-theme",t);
  document.getElementById("theme-btn").innerHTML=t==="dark"?"&#9728;":"&#127769;";
  localStorage.setItem("theme",t);
}
// Sync button icon to current theme on load
(function(){
  var t=document.documentElement.getAttribute("data-theme")||"light";
  document.getElementById("theme-btn").innerHTML=t==="dark"?"&#9728;":"&#127769;";
})();
var idx=0;
function styleContent(){
  document.querySelectorAll('#dw .rc').forEach(function(el){
    el.innerHTML=el.innerHTML
      .replace(/^RECAP:\\s*/i,'')
      .replace(/<br>\\s*KEY PLAYERS:\\s*<br>/i,'<div class="kp-hdr">Key Players</div>');
  });
}
function updateNav(){
  document.getElementById('lbl-cur').textContent=fulls[idx];
  var pb=document.getElementById('btn-prev'),nb=document.getElementById('btn-next');
  if(idx<files.length-1){pb.disabled=false;document.getElementById('lbl-prev').textContent=shorts[idx+1]}
  else{pb.disabled=true;document.getElementById('lbl-prev').textContent=''}
  if(idx>0){nb.disabled=false;document.getElementById('lbl-next').textContent=shorts[idx-1]}
  else{nb.disabled=true;document.getElementById('lbl-next').textContent=''}
}
async function navigate(dir){
  var ni=idx+dir;
  if(ni<0||ni>=files.length)return;
  idx=ni;
  var dw=document.getElementById('dw');
  dw.style.opacity='.5';
  try{
    var r=await fetch(files[idx]);
    var h=await r.text();
    var p=new DOMParser();
    var d=p.parseFromString(h,'text/html');
    var b=d.querySelector('body');
    if(b)dw.innerHTML=b.innerHTML;
  }catch(e){}
  dw.style.opacity='1';
  styleContent();
  window.scrollTo(0,0);
  updateNav();
}
updateNav();
styleContent();
"""

    page = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '  <title>Daily Baseball Digest</title>\n'
        '  <link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">\n'
        '  <style>\n'
        + site_css + '\n'
        + digest_css + '\n'
        + override_css
        + '  </style>\n'
        + '  <script>document.documentElement.setAttribute("data-theme",localStorage.getItem("theme")||"light")</script>\n'
        + '</head>\n<body>\n'
        '  <nav class="site-nav">\n'
        '    <div class="brand">\n'
        '      <span class="brand-icon">&#9918;</span>\n'
        '      <div>\n'
        '        <div class="brand-title">Daily Baseball Digest</div>\n'
        '        <div class="brand-sub">MLB Stats API &nbsp;&#183;&nbsp; Groq AI &nbsp;&#183;&nbsp; Updated daily</div>\n'
        '      </div>\n'
        '    </div>\n'
        '    <div class="day-nav">\n'
        '      <button class="nav-btn" id="btn-prev" onclick="navigate(1)">&#8592; <span id="lbl-prev"></span></button>\n'
        + f'      <span class="nav-date" id="lbl-cur">{latest_full}</span>\n'
        + '      <button class="nav-btn" id="btn-next" onclick="navigate(-1)"><span id="lbl-next"></span> &#8594;</button>\n'
        + '    </div>\n'
        + '    <button class="theme-btn" id="theme-btn" onclick="toggleTheme()" title="Toggle dark mode">&#127769;</button>\n'
        + '  </nav>\n'
        '  <div id="dw">'
        + digest_body
        + '</div>\n'
        '  <script>\n'
        + js_files  + ';\n'
        + js_shorts + ';\n'
        + js_fulls  + ';\n'
        + js_core
        + '  </script>\n</body>\n</html>'
    )

    try:
        with open(os.path.join(out_dir, 'index.html'), 'w', encoding='utf-8') as fh:
            fh.write(page)
        print(f"  index.html updated")
    except Exception as e:
        print(f"  [warn] Could not write index.html: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    date_override = sys.argv[1] if len(sys.argv) > 1 else None
    max_games     = int(sys.argv[2]) if len(sys.argv) > 2 else None
    date_str, date_display = get_target_date(date_override)
    year = datetime.strptime(date_str, "%m/%d/%Y").year

    print(f"[MLB] Digest -- {date_display}")
    print("Fetching schedule and standings...")

    games = fetch_completed_games(date_str)
    if max_games:
        games = games[:max_games]
    team_records, team_streaks = fetch_team_records(year)

    if not games:
        print("No completed games found — sending off-day notice.")
        html = build_html_email(date_display, [])
        save_html_locally(html, date_str, date_display)
        send_email(f"⚾ Baseball Digest — {date_display} (Off Day)", html)
        print("Done.")
        return

    print(f"Found {len(games)} game(s). Generating summaries via Groq...\n")
    groq_client   = Groq(api_key=GROQ_API_KEY)
    summaries     = []
    token_totals  = {'prompt': 0, 'completion': 0}
    series_ctxs   = build_series_contexts(games, date_str)
    team_trends   = fetch_team_trends(date_str)

    for i, game in enumerate(games, 1):
        away     = game["away_name"]
        home     = game["home_name"]
        away_id  = game["away_id"]
        home_id  = game["home_id"]
        ascore   = game["away_score"]
        hscore   = game["home_score"]
        print(f"  [{i}/{len(games)}] {away} {ascore} @ {home} {hscore}")

        box_data                          = get_box_data(game["game_id"])
        key_moments, linescore            = get_key_moments(game["game_id"], away, home, int(ascore), int(hscore))
        summary                           = summarize_game(groq_client, game, box_data, token_totals, team_trends, key_moments)
        pitchers, top_batter, qs_pitchers = extract_display_stats(box_data, game)
        milestones                        = detect_milestones(box_data)

        winner        = away if int(ascore) > int(hscore) else home
        loser         = home if int(ascore) > int(hscore) else away
        winner_id     = away_id if int(ascore) > int(hscore) else home_id
        loser_id      = home_id if int(ascore) > int(hscore) else away_id
        run_diff      = abs(int(ascore) - int(hscore))
        innings       = int(game.get("current_inning") or 9)
        extra_innings = innings > 9

        # Native doubleheader label from the API (game_num is 1 or 2)
        dh = game.get("doubleheader", "N")
        game_num_label = f"Game {game['game_num']} of 2" if dh == "Y" else ""

        summaries.append({
            "matchup":        f"{away} @ {home}",
            "score":          f"{ascore}–{hscore}",
            "away_score_val": ascore,
            "home_score_val": hscore,
            "away_id":        away_id,
            "home_id":        home_id,
            "away_record":    team_records.get(away_id, ""),
            "home_record":    team_records.get(home_id, ""),
            "winner":         winner,
            "loser":          loser,
            "winner_streak":  team_streaks.get(winner_id, ""),
            "loser_streak":   team_streaks.get(loser_id, ""),
            "walkoff":        is_walkoff(game),
            "run_diff":       run_diff,
            "extra_innings":  extra_innings,
            "innings":        innings,
            "game_num":       game_num_label,
            "pitchers":        pitchers,
            "top_batter":      top_batter,
            "qs_pitchers":     qs_pitchers,
            "milestones":      milestones,
            "series_context":  series_ctxs.get(game["game_id"], ""),
            "linescore":       linescore,
            "summary":         summary,
        })

        if i < len(games):
            time.sleep(1)

    total_tokens = token_totals['prompt'] + token_totals['completion']
    print(f"\nGroq usage: {token_totals['prompt']} prompt + {token_totals['completion']} completion = {total_tokens} tokens total")
    print(f"Building email and sending to {RECIPIENT_EMAIL}...")
    html    = build_html_email(date_display, summaries)
    subject = f"⚾ Baseball Digest — {date_display} ({len(games)} games)"
    save_html_locally(html, date_str, date_display)
    try:
        send_email(subject, html)
        print("Email sent successfully!")
    except Exception as e:
        print(f"  [warn] Email failed: {e}")


if __name__ == "__main__":
    main()
