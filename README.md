# Correcteur EBP / FEC pour Pennylane

Application Streamlit autonome : toute la logique est dans **app.py**. Aucun import de `converter.py` ni autre module maison. Accepte l’export EBP détaillé fourni pour SEMG et les FEC avec colonnes Débit/Crédit. Ne lit pas les XLSX ni les exports « montant + sens ».

## Tester sur Streamlit Community Cloud, sans installation locale

1. Décompresser le ZIP.
2. Sur GitHub, créer un **nouveau dépôt** pour cet outil (ou une nouvelle branche dédiée). Ajouter `app.py` et `requirements.txt` à sa racine via **Add file → Upload files**, puis valider. Le README et les tests sont facultatifs pour l’hébergement. **Ne pas déposer les fichiers comptables dans GitHub.**
3. Ouvrir https://share.streamlit.io et créer une application avec ce dépôt et sa branche.
4. Indiquer **app.py** comme fichier principal. Dans les paramètres avancés, choisir **Python 3.12**.
5. Déployer. Les dépendances sont déclarées dans `requirements.txt`.
6. Ouvrir l’application et charger le CSV comptable directement dans son formulaire.

Documentation : https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app

## Utilisation

1. Charger un fichier à la fois. Laisser encodage et séparateur sur Auto pour le CSV SEMG.
2. Contrôler les totaux. L’application refuse les montants invalides et les décimales significatives au-delà du centime : elle ne les remplace pas par zéro.
3. Vérifier les journaux d’à-nouveaux (`[AN]` dans le fichier SEMG).
4. Examiner les groupes proposés. Les références de pièce déjà équilibrées sont regroupées à journal/date identiques. Pour les autres, l’application recherche des séquences équilibrées dans l’ordre des lignes restantes. Cela ne prouve pas leur identité comptable : la case de validation exige une revue.
5. S’il reste des écarts, vérifier les justificatifs et modifier les dates ou montants dans le tableau. Cliquer sur **Appliquer les corrections**. Les numéros de ligne affichés sont les rangs CSV logiques, avec l’en-tête compté comme ligne 1. Une cellule contenant un saut de ligne peut décaler le numéro physique dans un éditeur de texte.
6. Choisir les comptes à délettrer. **Par défaut aucun compte n’est délettré.** Le bouton de présélection cible tous les comptes hors préfixes `4`, `23`, `511`, `58`, mais certains peuvent être activés dans ton plan comptable Pennylane : revoir cette sélection. On peut aussi conserver ou retirer tous les lettrages. Le retrait efface le code et sa date, sans changer le compte ni son montant.
7. Si tu souhaites une migration provisoire malgré les écarts de dates, activer explicitement les ajouts en compte d’attente, puis vérifier et approuver le tableau des contreparties. Le compte choisi doit être inutilisé dans le fichier et le fichier doit être globalement équilibré. **Cette option ajoute de vraies lignes comptables et ne résout pas l’origine des écarts.** Elle est désactivée par défaut.
8. Valider les regroupements. Télécharger de préférence **FEC + rapport + trace des modifications**.
9. Dans Pennylane, utiliser le parcours **import FEC**, puis vérifier les écritures, balances, auxiliaires, banque, lettrages et, s’il est utilisé, le compte d’attente avant validation. L’acceptation finale dépend aussi du plan comptable et des règles du dossier cible.

## Ce que fait le convertisseur

- Conserve chaque ligne source, les numéros de compte complets et les caractères alphanumériques.
- Ne supprime pas les doublons apparents : deux lignes identiques peuvent être justifiées.
- Calcule en centimes entiers (conversion initiale avec Decimal).
- Ne rapproche jamais automatiquement des dates comptables différentes.
- Produit un numéro d’écriture de migration unique et une référence commune par groupe. Conserve les anciennes références modifiées dans le libellé et dans la trace.
- Regroupe les à-nouveaux par journal/date sans changer les dates des pièces historiques.
- Contrôle les mouvements par compte, le nombre de lignes et l’équilibre de chaque écriture avant export.
- Conserve les corrections manuelles dans la trace avec valeurs avant/après.
- Complète une date de pièce absente par la date comptable ; garde les dates de validation absentes vides.
- Exporte UTF-8 avec BOM, tabulations, montants à virgule et 18 colonnes FEC.
- Remplace les tabulations et sauts de ligne internes par des espaces dans le fichier de destination, avec traçabilité.

Les données sont traitées en mémoire sur le serveur hébergeant Streamlit. Le code n’enregistre pas les données comptables sur disque, n’utilise pas de cache partagé et ne les transmet pas à une API. Les changements de session sont perdus après fermeture/redémarrage : télécharger le résultat et sa trace.

## Limites importantes

Le CSV EBP n’a pas de numéro d’écriture source ni de date de validation. Les regroupements sont reconstruits pour une **migration**. Le résultat n’est donc pas présenté comme un FEC réglementaire original à remettre en contrôle fiscal. Pour celui-ci, privilégier l’export FEC officiel du logiciel d’origine. Les dates de validation ne sont jamais inventées.

Les 18 champs FEC ne transportent pas les pièces jointes, axes analytiques, échéances, moyens de paiement ni la notion EBP de lettrage partiel. Le fichier d’origine doit être conservé. Les libellés de ligne restent distincts ; l’import est prévu via le parcours FEC et non un mapping CSV générique regroupant par libellé.

L’application ne connaît pas les réglages de lettrage du dossier cible. Le compte exact des « 161 lignes » exige le fichier de résolution Pennylane ou la liste des comptes concernés. Elle n’invente pas cette correspondance.

Sources Pennylane consultées le 05/10/2026 :
- https://help.pennylane.com/fr/articles/18792-importer-un-fec
- https://help.pennylane.com/fr/articles/18751-lettrer-des-ecritures
- https://help.pennylane.com/fr/articles/18793-importer-des-ecritures

## Exécution locale facultative

Avec Python 3.12 :

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Tests du moteur (bibliothèque standard uniquement) :

```bash
python -m unittest -v
```
