"""Convertisseur EBP / FEC vers trame FEC de migration. Calculs exacts en centimes."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

FIELDS = 'JournalCode JournalLib EcritureNum EcritureDate CompteNum CompteLib CompAuxNum CompAuxLib PieceRef PieceDate EcritureLib Debit Credit EcritureLet DateLet ValidDate Montantdevise Idevise'.split()
DEFAULT_LETTERABLE = ('401', '411', '58')


def norm(value):
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in unicodedata.normalize('NFD', value.lower()) if unicodedata.category(c) != 'Mn'))


def money(value):
    """Refuse les pertes de précision et les valeurs manquantes mal formées."""
    s = str(value).strip().replace('\u00a0', '').replace('\u202f', '').replace(' ', '')
    if not s:
        return 0
    if not re.fullmatch(r'[+-]?\d+(?:[,.]\d+)?', s):
        raise ValueError(f'Montant invalide : {value!r}')
    try:
        v = Decimal(s.replace(',', '.')) * 100
    except InvalidOperation as exc:
        raise ValueError(f'Montant invalide : {value!r}') from exc
    if not v.is_finite() or v != v.to_integral_value():
        raise ValueError(f'Plus de deux décimales significatives : {value!r}. Aucun arrondi automatique.')
    if abs(v) > 10**16:
        raise ValueError('Montant hors plage.')
    return int(v)


def fmt(cents):
    sign = '-' if cents < 0 else ''
    cents = abs(cents)
    return f'{sign}{cents // 100},{cents % 100:02d}'


def date_value(value, required=False):
    value = str(value).strip()
    if not value and not required:
        return ''
    for form in ('%Y%m%d', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(value, form).strftime('%Y%m%d')
        except ValueError:
            pass
    raise ValueError(f'Date invalide : {value!r} (attendu JJ/MM/AAAA ou AAAAMMJJ)')


def parse_file(data, encoding='Auto', delimiter='Auto'):
    used_encoding = encoding
    if encoding == 'Auto':
        for used_encoding in ('utf-8-sig', 'cp1252'):
            try:
                text = data.decode(used_encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError('Encodage inconnu. Choisir un encodage manuellement.')
    else:
        text = data.decode(encoding)
    if '\x00' in text:
        raise ValueError('Fichier binaire ou UTF-16 : choisir UTF-16 si nécessaire.')
    sep = delimiter
    if delimiter == 'Auto':
        first = text.splitlines()[0] if text.splitlines() else ''
        sep = max((';', '\t', '|', ','), key=first.count)
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=sep, strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError('Fichier vide.')
    keys = [norm(h.lstrip('\ufeff')) for h in header]
    if len(set(keys)) != len(keys):
        raise ValueError('En-têtes dupliqués : corriger le fichier source.')
    aliases = {
        'JournalCode': ['Code journal'], 'JournalLib': ['Description du journal'],
        'EcritureDate': ['Date au format L47', 'Date'], 'CompteNum': ['N° de compte'],
        'CompteLib': ['Intitulé du compte'], 'PieceRef': ['Pièce'],
        'PieceDate': ['Date de pièce'], 'EcritureLib': ['Libellé'],
        'EcritureLet': ['Lettrage'], 'DateLet': ['Date de lettrage'],
    }
    mapping = {}
    for field in FIELDS:
        for candidate in [field] + aliases.get(field, []):
            if norm(candidate) in keys:
                mapping[field] = keys.index(norm(candidate))
                break
    required = ['JournalCode', 'EcritureDate', 'CompteNum', 'Debit', 'Credit']
    missing = [f for f in required if f not in mapping]
    if missing:
        raise ValueError('Colonnes manquantes : ' + ', '.join(missing) + '. Formats acceptés : export EBP détaillé et FEC à 18 colonnes.')
    rows, errors = [], []
    for source_id, values in enumerate(reader, 2):
        if not values or not any(v.strip() for v in values):
            continue
        if len(values) != len(header):
            errors.append(f'Ligne logique {source_id} : {len(values)} champs au lieu de {len(header)}.')
            continue
        r = {f: values[mapping[f]].strip() if f in mapping else '' for f in FIELDS}
        try:
            for f in ('EcritureDate', 'PieceDate', 'DateLet', 'ValidDate'):
                r[f] = date_value(r[f], required=f == 'EcritureDate')
            r['_debit'], r['_credit'] = money(r['Debit']), money(r['Credit'])
            if r['_debit'] < 0 or r['_credit'] < 0:
                raise ValueError('Débit/crédit négatif : inverser le sens dans la source avant conversion.')
            if r['_debit'] and r['_credit']:
                raise ValueError('Débit et crédit tous deux non nuls sur la même ligne.')
            if not r['JournalCode'] or not r['CompteNum']:
                raise ValueError('Journal ou compte vide.')
            r['_id'] = source_id
            r['_original'] = dict(r)
            rows.append(r)
        except ValueError as exc:
            errors.append(f'Ligne logique {source_id} : {exc}')
    if errors:
        raise ValueError('\n'.join(errors[:30]) + (f'\n… {len(errors)} erreurs au total.' if len(errors) > 30 else ''))
    if not rows:
        raise ValueError('Aucune écriture.')
    fec_input = 'EcritureNum' in mapping
    if fec_input and any(not r['EcritureNum'] for r in rows):
        raise ValueError('Le FEC contient des numéros d’écriture vides. Corriger la source.')
    return rows, {'encoding': used_encoding, 'delimiter': repr(sep), 'format': 'FEC' if fec_input else 'EBP', 'rows': len(rows)}


def balance(rows):
    return sum(r['_debit'] - r['_credit'] for r in rows)


def daily(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r['JournalCode'], r['EcritureDate'])].append(r)
    return [{'Journal': k[0], 'Date': k[1], 'Lignes': len(v), 'Écart D-C': fmt(balance(v))}
            for k, v in sorted(groups.items()) if balance(v)]


def make_groups(rows, is_fec=False, opening=(), reconstruct=True):
    """Ne mélange jamais deux dates. Les regroupements EBP restent des propositions."""
    if is_fec:
        grouped = defaultdict(list)
        for r in rows:
            grouped[(r['JournalCode'], r['EcritureNum'])].append(r)
        return [{'rows': v, 'method': 'Numéro source'} for v in grouped.values()]
    days = defaultdict(list)
    for r in rows:
        days[(r['JournalCode'], r['EcritureDate'])].append(r)
    result = []
    for (journal, _), items in days.items():
        if journal in opening:
            result.append({'rows': items, 'method': 'À-nouveaux par date'})
            continue
        pieces = defaultdict(list)
        for r in items:
            pieces[r['PieceRef']].append(r)
        consumed = set()
        for piece, part in pieces.items():
            if not reconstruct or (piece and not balance(part)):
                result.append({'rows': part, 'method': 'Pièce source'})
                consumed.update(r['_id'] for r in part)
        pending = []
        for r in items:
            if r['_id'] in consumed:
                continue
            pending.append(r)
            if not balance(pending):
                result.append({'rows': pending, 'method': 'Séquence équilibrée proposée'})
                pending = []
        if pending:
            result.append({'rows': pending, 'method': 'Reliquat à corriger'})
    return sorted(result, key=lambda g: (g['rows'][0]['EcritureDate'], min(r['_id'] for r in g['rows'])))


def group_table(groups):
    return [{'Groupe': i + 1, 'Journal': g['rows'][0]['JournalCode'],
             'Date': ', '.join(sorted({r['EcritureDate'] for r in g['rows']})),
             'Méthode': g['method'], 'Lignes': len(g['rows']), 'Écart D-C': fmt(balance(g['rows'])),
             'Première ligne': min(r['_id'] for r in g['rows'])}
            for i, g in enumerate(groups)]


def csv_bytes(rows, fields=None, sep=';'):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0] if rows else {'Information': ''}), delimiter=sep, lineterminator='\r\n', extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def export_fec(groups, remove_accounts=(), remove_all=False, suspense=None, journal_map=None, rebuild_pieces=True):
    journal_map = journal_map or {}
    output, audit, additions = [], [], []
    if any(len({r['EcritureDate'] for r in g['rows']}) != 1 for g in groups):
        raise ValueError('Une écriture couvre plusieurs dates. Corriger ses dates avant export.')
    originals = [r for g in groups for r in g['rows']]
    if len({r['_id'] for r in originals}) != len(originals):
        raise ValueError('Une ligne est affectée à plusieurs groupes.')
    if suspense:
        if not re.fullmatch(r'471\d{3,9}', suspense):
            raise ValueError('Choisir un compte d’attente numérique de 6 à 12 caractères commençant par 471.')
        if any(r['CompteNum'] == suspense for r in originals):
            raise ValueError('Ce compte d’attente existe déjà dans le fichier : choisir un compte dédié inutilisé.')
        if balance(originals):
            raise ValueError('Le fichier est globalement déséquilibré : passage provisoire interdit, corriger la source.')
    elif any(balance(g['rows']) for g in groups):
        raise ValueError('Il reste des groupes déséquilibrés. Export bloqué.')
    for i, g in enumerate(sorted(groups, key=lambda g: (g['rows'][0]['EcritureDate'], min(r['_id'] for r in g['rows']))), 1):
        items = g['rows']
        number = f'MIG{i:08d}'
        refs = {r['PieceRef'] for r in items if r['PieceRef']}
        piece = number if rebuild_pieces else (next(iter(refs)) if len(refs) == 1 else number)
        for src in items:
            r = {f: src[f] for f in FIELDS}
            r['JournalCode'] = journal_map.get(src['JournalCode'], src['JournalCode'])
            r['JournalLib'] = r['JournalLib'] or r['JournalCode']
            r['CompteLib'] = r['CompteLib'] or r['CompteNum']
            r['EcritureLib'] = r['EcritureLib'] or 'Reprise comptable'
            r['EcritureNum'], r['PieceRef'] = number, piece
            if src['PieceRef'] and src['PieceRef'] != piece:
                r['EcritureLib'] += ' | Réf. source : ' + src['PieceRef']
            r['PieceDate'] = r['PieceDate'] or r['EcritureDate']
            r['Debit'], r['Credit'] = fmt(src['_debit']), fmt(src['_credit'])
            if remove_all or src['CompteNum'] in remove_accounts:
                r['EcritureLet'] = r['DateLet'] = ''
            elif not r['EcritureLet']:
                r['DateLet'] = ''
            for field in FIELDS:
                r[field] = re.sub(r'[\t\r\n]+', ' ', r[field])
            old = src.get('_original', src)
            changes = {f: {'avant': old.get(f, ''), 'après': r[f]} for f in FIELDS if str(old.get(f, '')) != r[f]}
            audit.append({'Ligne source': src['_id'], 'EcritureNum': number, 'Méthode': g['method'],
                          'Compte': r['CompteNum'], 'Modifications': json.dumps(changes, ensure_ascii=False)})
            output.append(r)
        diff = balance(items)
        if diff:
            src = items[0]
            r = {f: '' for f in FIELDS}
            r.update(JournalCode=journal_map.get(src['JournalCode'], src['JournalCode']), JournalLib=src['JournalLib'] or src['JournalCode'],
                     EcritureNum=number, EcritureDate=src['EcritureDate'], CompteNum=suspense,
                     CompteLib='Attente migration à justifier', PieceRef=piece, PieceDate=src['EcritureDate'],
                     EcritureLib='PROVISOIRE — écart de migration à justifier', Debit=fmt(max(-diff, 0)), Credit=fmt(max(diff, 0)))
            output.append(r)
            additions.append(r)
            audit.append({'Ligne source': 'AJOUT', 'EcritureNum': number, 'Méthode': 'Passage provisoire explicite',
                          'Compte': suspense, 'Modifications': json.dumps(r, ensure_ascii=False)})
    checks = defaultdict(int)
    for r in output:
        checks[(r['JournalCode'], r['EcritureNum'], r['EcritureDate'])] += money(r['Debit']) - money(r['Credit'])
    if any(checks.values()):
        raise ValueError('Contrôle final : une écriture est déséquilibrée.')
    # Conservation exacte des mouvements et des comptes des lignes source.
    before, after = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for r in originals:
        before[r['CompteNum']][0] += r['_debit']
        before[r['CompteNum']][1] += r['_credit']
    for r in output:
        after[r['CompteNum']][0] += money(r['Debit'])
        after[r['CompteNum']][1] += money(r['Credit'])
    for account, totals in before.items():
        if totals != after[account]:
            raise ValueError('Contrôle final : mouvements source modifiés.')
    return output, audit, additions


def bundle(output, audit, additions, report):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('FEC_migration.txt', csv_bytes(output, FIELDS, '\t'))
        z.writestr('Trace_modifications.csv', csv_bytes(audit))
        z.writestr('Lignes_provisoires.csv', csv_bytes(additions, FIELDS))
        z.writestr('Rapport.txt', report)
    return data.getvalue()


def main():
    import pandas as pd
    import streamlit as st
    st.set_page_config(page_title='Correction import Pennylane', page_icon='📒', layout='wide')
    st.title('Correction d’import Pennylane')
    st.write('Export EBP détaillé ou FEC → contrôle des pièces, choix du lettrage et fichier de migration à 18 colonnes.')
    st.caption('Les calculs utilisent des centimes exacts. Aucune suppression de ligne, aucun raccourcissement de compte, aucun arrondi silencieux.')
    upload = st.file_uploader('1. Charger un CSV ou FEC', type=['csv', 'txt', 'fec'])
    with st.expander('Lecture du fichier'):
        encoding = st.selectbox('Encodage', ['Auto', 'utf-8-sig', 'cp1252', 'utf-16'])
        delimiter = st.selectbox('Séparateur', ['Auto', ';', '\t', '|', ','])
    if upload is None:
        st.info('Compatible notamment avec « SEMG 2021 2022 EXPORT PARAM DEF.CSV ». Le fichier comptable se charge ici, après le déploiement de l’application.')
        return
    data = upload.getvalue()
    fingerprint = hashlib.sha256(data + (encoding + delimiter).encode()).hexdigest()[:16]
    try:
        rows, info = parse_file(data, encoding, delimiter)
    except (ValueError, UnicodeError, csv.Error) as exc:
        st.error(str(exc))
        return
    totals = sum(r['_debit'] for r in rows), sum(r['_credit'] for r in rows)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Lignes source', f'{len(rows):,}'.replace(',', ' '))
    c2.metric('Débit', fmt(totals[0]) + ' €')
    c3.metric('Crédit', fmt(totals[1]) + ' €')
    c4.metric('Écart global', fmt(totals[0] - totals[1]) + ' €')
    st.caption(f"Lecture {info['format']} · {info['encoding']} · dates du {min(r['EcritureDate'] for r in rows)} au {max(r['EcritureDate'] for r in rows)}")
    st.subheader('2. Regrouper les lignes')
    journals = sorted({r['JournalCode'] for r in rows})
    opening = st.multiselect('Journaux d’à-nouveaux : une écriture par date', journals,
                             default=[j for j in journals if norm(j) in ('an', 'ran')], key='an' + fingerprint)
    reconstruct = st.checkbox('Proposer des séquences équilibrées pour les références absentes ou incohérentes', value=True, disabled=info['format'] == 'FEC')
    st.caption('Les pièces déjà équilibrées sont gardées ensemble. Les lignes restantes sont regroupées dans leur ordre source, à date et journal identiques. Un équilibre arithmétique reste une proposition à vérifier.')
    initial_groups = make_groups(rows, info['format'] == 'FEC', opening, reconstruct)
    bad_day = daily(rows)
    if bad_day:
        st.warning(f'{len(bad_day)} journées sont déséquilibrées. L’équilibre global ne suffit pas.')
        st.dataframe(pd.DataFrame(bad_day), hide_index=True)
    # Tous les groupes problématiques sont éditables, y compris un FEC multiday.
    bad_ids = {r['_id'] for g in initial_groups if balance(g['rows']) or len({r['EcritureDate'] for r in g['rows']}) > 1 for r in g['rows']}
    bad_keys = {(d['Journal'], d['Date']) for d in bad_day}
    with st.expander('Corriger des dates ou montants après vérification des justificatifs', expanded=bool(bad_ids)):
        show_all = st.checkbox('Afficher toutes les lignes (sinon seulement les anomalies)', key='all' + fingerprint)
        selected = rows if show_all else [r for r in rows if r['_id'] in bad_ids or (r['JournalCode'], r['EcritureDate']) in bad_keys]
        view = [{'Ligne': r['_id'], 'Journal': r['JournalCode'], 'Compte': r['CompteNum'], 'Pièce': r['PieceRef'],
                 'Libellé': r['EcritureLib'], 'Date': r['EcritureDate'], 'Débit': fmt(r['_debit']), 'Crédit': fmt(r['_credit'])} for r in selected]
        if view:
            st.caption('Seules Date, Débit et Crédit sont modifiables. Appliquer, puis contrôler le résultat. Les changements sont conservés dans le rapport.')
            with st.form('corrections' + fingerprint + str(show_all)):
                saved = st.session_state.get('changes' + fingerprint, {})
                for line in view:
                    line.update(saved.get(line['Ligne'], {}))
                edited = st.data_editor(pd.DataFrame(view), hide_index=True, num_rows='fixed',
                                        disabled=['Ligne', 'Journal', 'Compte', 'Pièce', 'Libellé'], height=350)
                apply_changes = st.form_submit_button('Appliquer les corrections')
            if apply_changes:
                updates = dict(saved)
                try:
                    for record in edited.to_dict('records'):
                        date = date_value(record['Date'], required=True)
                        debit, credit = money(record['Débit']), money(record['Crédit'])
                        if debit < 0 or credit < 0 or (debit and credit):
                            raise ValueError('Montants négatifs ou débit et crédit simultanés.')
                        updates[int(record['Ligne'])] = {'Date': date, 'Débit': fmt(debit), 'Crédit': fmt(credit)}
                    st.session_state['changes' + fingerprint] = updates
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if st.button('Annuler toutes les corrections manuelles', key='reset' + fingerprint):
            st.session_state.pop('changes' + fingerprint, None)
            st.rerun()
    for r in rows:
        change = st.session_state.get('changes' + fingerprint, {}).get(r['_id'])
        if change:
            r['EcritureDate'] = change['Date']
            r['_debit'], r['_credit'] = money(change['Débit']), money(change['Crédit'])
    groups = make_groups(rows, info['format'] == 'FEC', opening, reconstruct)
    summary = group_table(groups)
    bad = [g for g in groups if balance(g['rows'])]
    multi_date = [g for g in groups if len({r['EcritureDate'] for r in g['rows']}) > 1]
    manual_count = sum(any(r[f] != r['_original'][f] for f in ('EcritureDate', '_debit', '_credit')) for r in rows)
    st.write(f'Après corrections : **{len(groups)} groupes**, **{len(bad)} déséquilibrés**, **{manual_count} lignes modifiées manuellement**. Écart global : **{fmt(balance(rows))} €**.')
    with st.expander('Contrôler les groupes et leurs lignes'):
        st.dataframe(pd.DataFrame(summary), hide_index=True)
        group_index = st.number_input('Groupe à examiner', min_value=1, max_value=len(groups), value=1)
        st.dataframe(pd.DataFrame([{f: r[f] for f in FIELDS} | {'Ligne source': r['_id'], 'Debit': fmt(r['_debit']), 'Credit': fmt(r['_credit'])} for r in groups[group_index - 1]['rows']]), hide_index=True)
    st.download_button('Télécharger le diagnostic des groupes', csv_bytes(summary), 'Diagnostic_groupes.csv', 'text/csv')
    rebuild_pieces = st.checkbox('Refabriquer les numéros de pièces : une référence unique par écriture', value=True)
    st.caption('Les références d’origine restent dans les libellés et le rapport. Aucune case de confirmation supplémentaire ne bloque le téléchargement.')
    st.subheader('3. Corriger les lettrages')
    lettered = Counter(r['CompteNum'] for r in rows if r['EcritureLet'])
    labels = {r['CompteNum']: r['CompteLib'] for r in rows}
    st.caption('Réglage demandé : conserver les lettrages des comptes commençant par 401, 411 et 58 ; retirer tous les autres par défaut.')
    mode = st.radio('Traitement', ['Comptes choisis', 'Conserver tous les lettrages', 'Retirer tous les lettrages'], horizontal=True)
    defaults = sorted(a for a in lettered if not a.startswith(DEFAULT_LETTERABLE))
    if st.button('Rétablir la règle : conserver uniquement 401 / 411 / 58', disabled=mode != 'Comptes choisis'):
        st.session_state['letters_v2' + fingerprint] = defaults
    remove = st.multiselect('Comptes à délettrer', sorted(lettered), default=defaults,
                            format_func=lambda a: f'{a} — {labels[a]} ({lettered[a]} lignes)', disabled=mode != 'Comptes choisis', key='letters_v2' + fingerprint)
    remove = remove if mode == 'Comptes choisis' else []
    st.write(f"Lettrages retirés : {sum(lettered.values()) if mode == 'Retirer tous les lettrages' else sum(lettered[a] for a in remove)} lignes. DateLet sera également vidée sur ces comptes.")
    with st.expander('Liste des comptes lettrés'):
        st.dataframe(pd.DataFrame([{'Compte': a, 'Libellé': labels[a], 'Lignes lettrées': n, 'Lettrage conservé par la règle': a.startswith(DEFAULT_LETTERABLE)} for a, n in sorted(lettered.items())]), hide_index=True)
    st.subheader('4. Exporter')
    suspense = None
    if bad:
        st.info('Les écarts restants sont compensés par défaut dans le compte 471999 pour permettre le téléchargement.')
        use_suspense = st.checkbox('Mettre les écarts en compte 471 et permettre le téléchargement', value=True)
        if use_suspense:
            suspense = st.text_input('Compte d’attente inutilisé', value='471999')
            st.warning('Une contrepartie sera ajoutée à chaque groupe déséquilibré. Les dates et montants source restent inchangés. Le compte se solde globalement si le fichier est équilibré, mais ses mouvements et soldes intermédiaires restent à justifier. Ce mécanisme ne répare pas la cause de l’écart.')
            st.dataframe(pd.DataFrame([{'Journal': g['rows'][0]['JournalCode'], 'Date': g['rows'][0]['EcritureDate'], 'Compte ajouté': suspense,
                                      'Débit ajouté': fmt(max(-balance(g['rows']), 0)), 'Crédit ajouté': fmt(max(balance(g['rows']), 0))} for g in bad]), hide_index=True)
            accept_suspense = True
        else:
            accept_suspense = False
    else:
        accept_suspense = True
    with st.expander('Codes journaux de destination (facultatif)'):
        st.caption('Conserver par défaut les codes source. Modifier uniquement pour correspondre aux journaux du dossier cible. Deux journaux distincts ne peuvent pas être fusionnés ici.')
        journal_frame = st.data_editor(pd.DataFrame([{'Source': j, 'Destination': j} for j in journals]), disabled=['Source'], hide_index=True, key='journals' + fingerprint)
    journal_map = {v['Source']: str(v['Destination']).strip() for v in journal_frame.to_dict('records')}
    valid_journals = all(journal_map.values()) and len(set(journal_map.values())) == len(journal_map) and all(not re.search(r'[\t\r\n|;]', v) for v in journal_map.values())
    if not valid_journals:
        st.error('Codes journaux vides, dupliqués ou contenant un séparateur.')
    missing_validation = sum(not r['ValidDate'] for r in rows)
    if missing_validation:
        st.warning(f'{missing_validation} lignes sans date de validation source : ValidDate restera vide. Ce fichier est une trame FEC de migration, pas un FEC fiscal certifié. Aucun numéro ni date de validation historique ne peut être reconstitué avec certitude.')
    else:
        st.info('Les numéros d’écriture de migration sont régénérés. Conserver le FEC source pour sa valeur historique et réglementaire.')
    ready = not multi_date and valid_journals and not balance(rows) and (not bad or (suspense and accept_suspense))
    if multi_date:
        st.error('Des écritures source couvrent plusieurs dates : corriger les dates, aucun passage d’attente ne résout cela.')
    if not ready:
        st.info('Pour télécharger, activer le passage des écarts en 471 ou corriger les anomalies signalées.')
        return
    try:
        output, audit, additions = export_fec(groups, remove, mode == 'Retirer tous les lettrages', suspense, journal_map, rebuild_pieces)
    except ValueError as exc:
        st.error(str(exc))
        return
    report = '\n'.join([
        'TRAME FEC DE MIGRATION — à contrôler dans Pennylane avant validation',
        f'Source : {upload.name}', f'SHA256 : {hashlib.sha256(data).hexdigest()}',
        f'Lignes source : {len(rows)} ; exportées : {len(output)} ; ajouts provisoires : {len(additions)}',
        f'Débit source : {fmt(totals[0])} ; Crédit source : {fmt(totals[1])}',
        f'Débit export : {fmt(sum(money(r["Debit"]) for r in output))} ; Crédit export : {fmt(sum(money(r["Credit"]) for r in output))}',
        f'Lignes modifiées manuellement : {manual_count}',
        f'Lettrage : {mode} ; comptes délettrés : {", ".join(remove)}',
        f'Compte d’attente : {suspense or "aucun"}',
        'Les regroupements sont reconstruits ; les pièces sont renumérotées selon le réglage choisi.',
        'Références source modifiées conservées dans les libellés et Trace_modifications.csv.',
        'Dates de pièces manquantes complétées avec la date comptable. Dates de validation manquantes laissées vides.',
        'Aucun raccourcissement des comptes ; aucun doublon supprimé ; aucune ligne source exclue.',
        'Les informations analytiques, pièces jointes et lettrages partiels spécifiques EBP ne sont pas portés par les 18 colonnes FEC.',
        'Importer via le parcours FEC, contrôler balance, auxiliaires, banque et compte d’attente avant validation.',
    ])
    st.success(f'{len(output)} lignes exportables ; toutes les écritures sont équilibrées au centime.')
    st.download_button('Télécharger le FEC de migration', csv_bytes(output, FIELDS, '\t'), 'FEC_migration.txt', 'text/plain')
    st.download_button('Télécharger le FEC + rapport + trace des modifications', bundle(output, audit, additions, report), 'FEC_et_controles.zip', 'application/zip')
    st.caption('Les fichiers sont traités en mémoire par le serveur de ton application. Le code n’envoie aucune donnée à un service externe et n’écrit aucun fichier comptable sur disque.')


if __name__ == '__main__':
    main()
