# cost-per-kept-meeting

Compare B2B lead-generation sources (channels or vendors) on the **cost per kept qualified meeting** instead of the cost per lead. A small, single-file Python tool (standard library only, Python 3.9+): it reads a CSV file, prints a ranking, and never contacts any service.

Method article: <https://altavista360.com/blog/cout-par-rendez-vous-generation-leads/>

## What and why

A cheap lead is not a cheap meeting. A source can deliver leads at a very low price and still be the most expensive one once you count the meetings that were booked but cancelled or missed, and the meetings that took place with someone who was not a qualified buyer.

The method, over one period and for each source:

1. Add up **all** acquisition costs: media, data, tools, human time, other fees.
2. Divide by the number of **qualified meetings that actually took place**. A meeting that was booked and then cancelled or missed (no-show) does not count as kept.
3. Rank sources by that figure, lowest first.
4. Do not stop there: a low cost per meeting does not prove profitability. When deals and margin are provided, the tool also reports the cost per closed deal and the margin minus the acquisition cost.

Reported for each source and period: total cost, cost per lead, show rate (kept / booked), cost per kept meeting, cost per qualified kept meeting, cost per deal, margin minus cost.

## Quick start

```sh
python3 cost_per_kept_meeting.py examples/sources.csv
python3 cost_per_kept_meeting.py examples/sources.csv --json
python3 cost_per_kept_meeting.py examples/sources.csv --csv
python3 cost_per_kept_meeting.py examples/sources.csv --min-meetings 10 --lang fr
```

Options:

| Option | Meaning |
|---|---|
| `--json` | Print JSON instead of the table. |
| `--csv` | Print CSV instead of the table (`--json` and `--csv` cannot be combined). |
| `--min-meetings N` | Flag results based on fewer than `N` qualified kept meetings as "small sample" (default `5`). They stay in the ranking: they are flagged, never hidden. |
| `--lang en\|fr` | Labels of the text table (default `en`). JSON and CSV keys never change. |

The file argument `-` reads standard input. The module can also be imported (`load_table`, `analyse`, `render_text`, `render_json`, `render_csv`).

Run the tests with `python3 -m unittest discover -s tests -v`.

## Input columns

UTF-8 CSV with a header row. Column names are case-insensitive; the order does not matter.

| Column | Required | Meaning |
|---|---|---|
| `source` | yes | Channel or vendor name. |
| `period` | no | Free text such as `2026-10`. Leave it out or empty to merge all periods of a source. |
| `cost_media` | at least one cost column | Advertising or media spend. |
| `cost_data` | | Purchased or enriched data. |
| `cost_tools` | | Software and tooling attributed to the source. |
| `cost_human_time` | | Time of the people who work the source, costed by you. |
| `cost_other` | | Any other fee (agency, partner, event...). |
| `leads` | no | Leads delivered. |
| `meetings_booked` | no | Meetings booked. |
| `meetings_kept` | no | Meetings that actually took place (not cancelled, not missed). |
| `meetings_qualified_kept` | yes | Kept meetings that met your qualification criteria. |
| `deals_closed` | no | Deals closed from those meetings. |
| `gross_margin` | no | Gross margin of those deals. Empty means "not provided". |

Rules:

- Costs, counts and margin are non-negative numbers with a dot as decimal separator. Counts are whole numbers. Money is handled with `decimal.Decimal`; values are rounded to 2 decimals (half up) only when displayed.
- In a column you keep, an empty cell means `0` (except `gross_margin`, where it means "not provided"). A column you do not track can be left out entirely: the metrics that need it show `n/a`.
- Several rows with the same `source` and `period` are summed (surrounding spaces in `source` are ignored; the comparison is otherwise exact).
- Validation is strict. Each of these is an input error naming the row: a negative or non-numeric number, a fractional count, an empty `source`, a row with the wrong number of cells, `meetings_kept` greater than `meetings_booked`, `meetings_qualified_kept` greater than `meetings_kept`, `deals_closed` greater than `meetings_qualified_kept`. When a funnel column is absent, a column is compared with the nearest funnel column that is present. Every faulty row is reported (the first 50 messages are shown).
- Row numbers are file line numbers: the header is row 1, the first data row is row 2.
- Unknown columns are ignored with a warning on standard error.

## Example

`examples/sources.csv` contains **fictitious numbers** for four invented sources. Do not read them as benchmarks.

```text
Cost per kept qualified meeting, lowest first (amounts in the currency of the input file)

| Rank | Source                    | Period  | Total cost | Cost per lead | Show rate | Cost per kept meeting | Cost per qualified kept meeting | Qualified kept | Cost per deal | Margin minus cost | Note |
| ---: | :------------------------ | :------ | ---------: | ------------: | --------: | --------------------: | ------------------------------: | -------------: | ------------: | ----------------: | :--- |
|    1 | Example Partner Referrals | 2026-10 |    2700.00 |         45.00 |    87.50% |                128.57 |                          180.00 |             15 |        675.00 |           8300.00 |      |
|    2 | Example Paid Search       | 2026-10 |    4800.00 |         20.00 |    83.33% |                120.00 |                          200.00 |             24 |        960.00 |           9200.00 |      |
|    3 | Example Outbound Calling  | 2026-10 |    4050.00 |         15.00 |    66.67% |                112.50 |                          337.50 |             12 |       2025.00 |           1950.00 |      |
|    4 | Example Purchased Lists   | 2026-10 |    4500.00 |          3.00 |    55.00% |                136.36 |                          500.00 |              9 |       4500.00 |          -2000.00 |      |

A low cost per meeting does not prove profitability: check closed deals and margin minus cost.
```

What the example shows: ranked by cost per lead, the order is exactly reversed (Purchased Lists 3.00, Outbound Calling 15.00, Paid Search 20.00, Partner Referrals 45.00). Purchased Lists is the cheapest per lead and the most expensive per qualified kept meeting, and its margin minus cost is negative. Outbound Calling is the cheapest per kept meeting, yet not per *qualified* kept meeting. Paid Search is made of two rows that are summed.

With `--min-meetings 10`, Purchased Lists (9 qualified kept meetings) gets the note "small sample" and stays in the table. A source with no qualified kept meeting is never divided by zero: it is listed last with the note "no qualified kept meeting", and `null` in JSON.

JSON and CSV carry the same figures with fixed English keys and a `status` code (`ok`, `small_sample`, `no_qualified_kept_meeting`). Amounts are strings rounded to 2 decimals, unavailable values are `null` (empty in CSV), and the order is deterministic.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `2` | Invalid input: unreadable file, not UTF-8, bad header, bad row (the message names the row), or an invalid option. |

## Limits

- A ranking over one period is not a causal attribution. Sources overlap (a prospect can be touched by several of them), and one month can be noisy: look at the small-sample flag and repeat over several periods.
- Check closed deals and margin before moving budget. The cost per meeting is a means, not the goal; sales cycles are often longer than a period, so deals of a row are expected to come from the meetings of the same row. To follow a long cycle, merge a longer window by leaving `period` empty.
- Qualification is your own definition and must be the same for every source compared. The tool trusts the counts it is given and cannot check them.
- Costs are as complete as the figures you provide, human time included. All amounts must be in one currency; nothing is converted.
- The numbers in `examples/` are fictitious.

---

# cost-per-kept-meeting (français)

Comparez des sources de génération de leads B2B (canaux ou prestataires) sur le **coût par rendez-vous qualifié tenu** plutôt que sur le coût par lead. Un petit outil Python en un seul fichier (bibliothèque standard uniquement, Python 3.9+) : il lit un fichier CSV, affiche un classement et ne contacte aucun service.

Article de méthode : <https://altavista360.com/blog/cout-par-rendez-vous-generation-leads/>

## Quoi et pourquoi

Un lead bon marché n'est pas un rendez-vous bon marché. Une source peut livrer des leads à très bas prix et rester la plus chère une fois comptés les rendez-vous pris puis annulés ou manqués, et ceux qui ont eu lieu avec un interlocuteur qui n'était pas un acheteur qualifié.

La méthode, sur une période et pour chaque source :

1. Additionner **tous** les coûts d'acquisition : média, données, outils, temps humain, autres frais.
2. Diviser par le nombre de **rendez-vous qualifiés qui ont réellement eu lieu**. Un rendez-vous pris puis annulé ou manqué (no-show) n'est pas un rendez-vous tenu.
3. Classer les sources selon ce chiffre, du plus bas au plus haut.
4. Ne pas s'arrêter là : un faible coût par rendez-vous ne prouve pas la rentabilité. Quand les affaires conclues et la marge sont fournies, l'outil donne aussi le coût par affaire conclue et la marge moins le coût d'acquisition.

Pour chaque source et chaque période : coût total, coût par lead, taux de présence (tenus / pris), coût par rendez-vous tenu, coût par rendez-vous qualifié tenu, coût par affaire, marge moins coût.

## Démarrage rapide

```sh
python3 cost_per_kept_meeting.py examples/sources.csv
python3 cost_per_kept_meeting.py examples/sources.csv --json
python3 cost_per_kept_meeting.py examples/sources.csv --csv
python3 cost_per_kept_meeting.py examples/sources.csv --min-meetings 10 --lang fr
```

| Option | Signification |
|---|---|
| `--json` | Affiche du JSON au lieu du tableau. |
| `--csv` | Affiche du CSV au lieu du tableau (`--json` et `--csv` sont exclusifs). |
| `--min-meetings N` | Signale « petit échantillon » les résultats fondés sur moins de `N` rendez-vous qualifiés tenus (défaut `5`). Ils restent dans le classement : signalés, jamais masqués. |
| `--lang en\|fr` | Libellés du tableau texte (défaut `en`). Les clés JSON et CSV ne changent jamais. |

L'argument `-` lit l'entrée standard. Tests : `python3 -m unittest discover -s tests -v`.

## Colonnes du fichier

CSV en UTF-8 avec une ligne d'en-tête. Les noms de colonnes ne sont pas sensibles à la casse ; l'ordre est libre.

| Colonne | Obligatoire | Signification |
|---|---|---|
| `source` | oui | Nom du canal ou du prestataire. |
| `period` | non | Texte libre, par exemple `2026-10`. Absente ou vide : toutes les périodes d'une source sont fusionnées. |
| `cost_media` | au moins une colonne de coût | Dépense média ou publicitaire. |
| `cost_data` | | Données achetées ou enrichies. |
| `cost_tools` | | Logiciels et outils attribués à la source. |
| `cost_human_time` | | Temps des personnes qui travaillent la source, valorisé par vous. |
| `cost_other` | | Tout autre frais (agence, partenaire, événement...). |
| `leads` | non | Leads livrés. |
| `meetings_booked` | non | Rendez-vous pris. |
| `meetings_kept` | non | Rendez-vous qui ont réellement eu lieu (ni annulés, ni manqués). |
| `meetings_qualified_kept` | oui | Rendez-vous tenus qui répondent à vos critères de qualification. |
| `deals_closed` | non | Affaires conclues issues de ces rendez-vous. |
| `gross_margin` | non | Marge brute de ces affaires. Vide : « non fournie ». |

Règles :

- Coûts, effectifs et marge sont des nombres positifs ou nuls, avec un point comme séparateur décimal. Les effectifs sont des entiers. L'argent est traité en `decimal.Decimal` ; l'arrondi à 2 décimales (au plus proche, moitié vers le haut) n'a lieu qu'à l'affichage.
- Dans une colonne présente, une cellule vide vaut `0` (sauf `gross_margin` : « non fournie »). Une colonne que vous ne suivez pas peut être omise : les indicateurs qui en dépendent affichent `n/d`.
- Plusieurs lignes de même `source` et même `period` sont additionnées (les espaces autour de `source` sont ignorés ; la comparaison est sinon exacte).
- La validation est stricte. Chacun de ces cas est une erreur d'entrée qui nomme la ligne : nombre négatif ou non numérique, effectif fractionnaire, `source` vide, nombre de cellules différent de l'en-tête, `meetings_kept` supérieur à `meetings_booked`, `meetings_qualified_kept` supérieur à `meetings_kept`, `deals_closed` supérieur à `meetings_qualified_kept`. Quand une colonne de l'entonnoir est absente, la comparaison se fait avec la colonne présente la plus proche. Toutes les lignes fautives sont signalées (les 50 premiers messages sont affichés).
- Les numéros de ligne sont ceux du fichier : l'en-tête est la ligne 1, la première ligne de données est la ligne 2.
- Les colonnes inconnues sont ignorées avec un avertissement sur la sortie d'erreur.

## Exemple

`examples/sources.csv` contient des **chiffres fictifs** pour quatre sources inventées. Ce ne sont pas des références.

```text
Coût par rendez-vous qualifié tenu, du plus bas au plus haut (montants dans la devise du fichier d'entrée)

| Rang | Source                    | Période | Coût total | Coût par lead | Taux de présence | Coût par RDV tenu | Coût par RDV qualifié tenu | RDV qualifiés tenus | Coût par affaire | Marge moins coût | Note |
| ---: | :------------------------ | :------ | ---------: | ------------: | ---------------: | ----------------: | -------------------------: | ------------------: | ---------------: | ---------------: | :--- |
|    1 | Example Partner Referrals | 2026-10 |    2700.00 |         45.00 |           87.50% |            128.57 |                     180.00 |                  15 |           675.00 |          8300.00 |      |
|    2 | Example Paid Search       | 2026-10 |    4800.00 |         20.00 |           83.33% |            120.00 |                     200.00 |                  24 |           960.00 |          9200.00 |      |
|    3 | Example Outbound Calling  | 2026-10 |    4050.00 |         15.00 |           66.67% |            112.50 |                     337.50 |                  12 |          2025.00 |          1950.00 |      |
|    4 | Example Purchased Lists   | 2026-10 |    4500.00 |          3.00 |           55.00% |            136.36 |                     500.00 |                   9 |          4500.00 |         -2000.00 |      |

Un faible coût par rendez-vous ne prouve pas la rentabilité : vérifiez les affaires conclues et la marge moins le coût.
```

Classées par coût par lead, les sources apparaissent dans l'ordre exactement inverse : la source la moins chère par lead (Example Purchased Lists, 3.00) est la plus chère par rendez-vous qualifié tenu (500.00) et sa marge moins coût est négative. Avec `--min-meetings 10`, cette source (9 rendez-vous qualifiés tenus) reçoit la note « petit échantillon » et reste dans le tableau. Une source sans aucun rendez-vous qualifié tenu n'est jamais divisée par zéro : elle est classée en dernier avec la note « aucun RDV qualifié tenu » (`null` en JSON).

Le JSON et le CSV portent les mêmes chiffres avec des clés anglaises fixes et un code `status` (`ok`, `small_sample`, `no_qualified_kept_meeting`). Les montants sont des chaînes arrondies à 2 décimales, les valeurs indisponibles sont `null` (cellule vide en CSV), l'ordre est déterministe.

## Codes de sortie

| Code | Signification |
|---|---|
| `0` | Succès. |
| `2` | Entrée invalide : fichier illisible, non UTF-8, en-tête ou ligne incorrects (le message nomme la ligne), ou option invalide. |

## Limites

- Un classement sur une seule période n'est pas une attribution causale. Les sources se recoupent (un prospect peut être touché par plusieurs d'entre elles) et un mois peut être bruité : regardez l'indicateur de petit échantillon et répétez sur plusieurs périodes.
- Vérifiez les affaires conclues et la marge avant de déplacer du budget. Le coût par rendez-vous est un moyen, pas le but ; les cycles de vente dépassent souvent une période, donc les affaires d'une ligne sont supposées issues des rendez-vous de la même ligne. Pour suivre un cycle long, fusionnez une fenêtre plus longue en laissant `period` vide.
- La qualification est votre propre définition et doit être la même pour toutes les sources comparées. L'outil fait confiance aux effectifs fournis et ne peut pas les vérifier.
- Les coûts sont aussi complets que les chiffres fournis, temps humain compris. Tous les montants doivent être dans une même devise ; rien n'est converti.
- Les chiffres de `examples/` sont fictifs.

Created by ALTA VISTA360, S.L.U. (`kanibal-studio`).

- [Altavista360](https://altavista360.com/)
- [Altavista Conseil](https://altavista-conseil.com/)
- [Kanibal Studio](https://infos.kanibal-studio.com/)

Licensed under the [MIT License](LICENSE).
