# Revue de code — Archive Station

- **Date** : 8 octobre 2026
- **Révision analysée** : `984a440` (branche `main`)
- **Périmètre** : backend Python, interface web, passerelle et scripts DSM, build
- **Méthode** :
  - lecture complète du code ;
  - exécution des tests et des linters ;
  - mesures sur une base SQLite synthétique ;
  - reproduction isolée des bugs suspectés ;
  - build hors ligne du `.spk`.

## Synthèse

Le projet est solide. Les fichiers ne sont publiés qu'une fois complets et vérifiés. Les reprises de téléchargement sont contrôlées. Les chemins sont protégés et les données affichées correctement échappées. Les 94 tests passent, et Ruff comme Prettier ne signalent rien.

Cette revue se limite à des **bugs réels dont la correction est courte**. Les améliorations d'architecture ou de process ont été écartées volontairement.

| # | Sujet | Gravité | Statut |
|---|---|---|---|
| 1 | Un thread de téléchargement meurt sur une exception imprévue | Haute | Reproduit |
| 2 | Fichier de taille inconnue bloqué par une réponse HTTP 416 | Haute | Reproduit |
| 3 | L'arrêt du paquet n'envoie jamais `SIGKILL` | Moyenne | Simulé |
| 4 | Les actions peuvent viser une autre tâche que celle affichée | Moyenne | Vérifié |
| 5 | `claim()` lent sur les très gros items | Moyenne | Mesuré |
| 6 | Rapports réécrits toutes les 15 s : les disques ne dorment plus | Moyenne | Vérifié |
| 7 | `service.log` grossit sans limite | Basse | Vérifié |
| 8 | Traductions : messages restés en français et pluriels faux | Moyenne | Vérifié |
| 9 | Rafraîchissement de l'interface | Basse | Vérifié |
| 10 | Petits bugs d'interface | Basse | Vérifié |
| 11 | Petites corrections diverses | Basse | Vérifié |

---

## 1. Un thread de téléchargement meurt sur une exception imprévue

**Où** : `src/archive_station/engine.py:79-127`

Seul `self.transfer(row)` est protégé par un `try`. Si `claim()`, `finish_jobs()` ou un `store.update()` placé dans un bloc `except` lève une exception, le thread s'arrête définitivement. Exemple : une erreur SQLite `database or disk is full`.

**Reproduit** : une seule `sqlite3.OperationalError` dans `claim()` arrête `download-0`. Avec `connections=1`, plus rien ne se télécharge jusqu'au redémarrage du paquet, alors que l'interface affiche toujours « En attente ».

**Correction** :

```python
while not self.stop.is_set():
    try:
        ...  # corps actuel
    except Exception:
        LOG.exception("Download worker iteration failed")
        self.stop.wait(5)
```

Si une ligne avait déjà été prise par `claim()`, il faut aussi remettre le fichier en `queued`, sur un chemin best-effort. Sinon il reste en `downloading` jusqu'au redémarrage et bloque le changement de destination par défaut.

## 2. Fichier de taille inconnue bloqué par une réponse HTTP 416

**Où** : `src/archive_station/engine.py:183-194`

**Cause** : un fichier partiel complet n'est détecté que si la taille est connue. Si un fichier de taille inconnue est interrompu après son dernier bloc mais avant d'être publié, la reprise demande `Range: bytes=N-` sur un fichier de N octets. Le serveur répond 416, et le fichier finit en erreur après les tentatives. L'interruption peut venir d'une pause, de la fin du créneau horaire ou d'un arrêt du paquet.

**Conséquences** :

- ni « Réessayer » ni « Réparer » ne débloquent la situation : il faut supprimer le `.part` à la main ;
- cela vise surtout `<identifier>_files.xml`, présent dans chaque item et listé sans taille.

**Reproduit** : 416 à chaque tentative.

**Fréquence** : la fenêtre est courte, donc c'est rare. Le blocage, lui, est définitif.

**Limites de la vérification** : la reproduction utilise un client simulé. Deux comportements d'Archive.org n'ont pas été vérifiés en direct :

- la réponse 416 à une requête `Range` qui commence à la fin du fichier, comportement standard de HTTP ;
- l'absence de taille pour `_files.xml` dans les métadonnées.

**Correction** : sur une réponse 416, supprimer le fichier partiel et repartir de 0. Ajouter un test dans `tests/test_downloads.py`.

## 3. L'arrêt du paquet n'envoie jamais `SIGKILL`

**Où** : `packaging/synology/scripts/start-stop-status:36-47` et `src/archive_station/reports.py:213`

`stop` attend environ 39 s, puis renvoie une erreur en laissant le processus vivant. L'arrêt Python peut dépasser ce délai : une lecture réseau bloquée peut durer 30 s, et `reports.shutdown()` attend son thread sans délai maximal. Dans ce cas, Arrêter, Mettre à jour et Désinstaller échouent dans le Centre de paquets.

**Correction** :

- envoyer `kill -9 "$pid"` en fin d'attente au lieu de `return 1` ;
- utiliser `kill "$pid" 2>/dev/null || true`, car `set -e` interrompt le script si le processus a disparu entre-temps ;
- remplacer `self.thread.join()` par `self.thread.join(timeout=10)` dans `reports.py`.

## 4. Les actions peuvent viser une autre tâche que celle affichée

**Où** : `static/app.js:856` et `static/app.js:458-466`

Cliquer sur une ligne ne vide pas les cases cochées, et les actions utilisent en priorité les tâches cochées.

**Scénario** : cocher A, puis cliquer sur la ligne B. B est affichée, mais Supprimer, Annuler et Pause agissent sur A.

**Correction** : vider `state.checked` lors d'un clic simple sur une ligne.

## 5. `claim()` lent sur les très gros items

**Où** : `src/archive_station/store.py:440-457`

La requête qui choisit le prochain fichier trie toute la file d'attente (`USE TEMP B-TREE FOR ORDER BY`), sous le verrou global. Ce n'est sensible qu'avec des dizaines de milliers de fichiers en attente. Mesuré avec 300 000 fichiers : **81 ms** par appel sur ce Mac, davantage sur un DS918+.

**Correction** (une dizaine de lignes, mesurée à **0,012 ms**) : choisir d'abord la tâche, puis son fichier.

```python
for (job_id,) in db.execute(
    "SELECT id FROM jobs WHERE status IN ('queued','running') "
    "ORDER BY priority DESC, queue_order, created"
):
    row = db.execute(
        "SELECT * FROM files INDEXED BY file_priority "
        "WHERE job_id=? AND status='queued' AND available_at<=? "
        "ORDER BY priority DESC, id LIMIT 1",
        (job_id, now),
    ).fetchone()
    if row:
        ...
```

## 6. Rapports réécrits toutes les 15 s : les disques ne dorment plus

**Où** : `src/archive_station/reports.py:231-235`

Les rapports des tâches en attente ou en cours sont réécrits avec `fsync` toutes les 15 s. Cela continue hors créneau horaire, alors que rien ne bouge : une tâche bloquée par le planning reste en `running`. Les disques du NAS ne peuvent alors jamais se mettre en veille.

**Correction** : appliquer à toutes les tâches le contrôle de signature déjà existant. Une tâche active ne serait alors réécrite que si ses compteurs ont changé.

Le README indique que le rapport est « refreshed approximately every 15 seconds » : il faudra adapter cette phrase.

## 7. `service.log` grossit sans limite

**Où** : `packaging/synology/scripts/start-stop-status:31` et `src/archive_station/__main__.py:51`

Chaque log est écrit dans `archive-station.log`, qui tourne, et aussi sur la sortie d'erreur. Cette sortie est ajoutée à `service.log`, qui ne tourne jamais.

**Correction** : retirer le `StreamHandler` quand `--dsm-auth` est actif.

## 8. Traductions : messages restés en français et pluriels faux

### Messages restés en français

Corrections simples, sans changer le fonctionnement des traductions :

- traduire `packaging/synology/WIZARD_UIFILES/install_uifile`, aujourd'hui identique à la version française ;
- ajouter aux catalogues les messages de `gateway.cgi`, en particulier « Archive Station n'est pas démarré… » et « Session DSM expirée… » ;
- ajouter aux catalogues les erreurs fréquentes du moteur, notamment « Transfert interrompu avant la fin du fichier. » ;
- passer par `t()` le message affiché en dur à `app.js:1082`.

### Pluriels

**Problème** : le code choisit la forme de deux façons.

- Avec un test `=== 1` entre une forme singulier et une forme pluriel (`app.js:421`, `:1135`).
- Avec une forme plurielle fixe :
  - `"{count} tâches"` (`app.js:480`, `:1026`, `:1048`) ;
  - `${n} ${t("fichiers")}` (`app.js:294`, `:320`, `:431`, `:531`, `:948`, `:1181`, `:1232`).

**Résultats faux** :

- en français comme en anglais : « 1 tâches », « 1 / 1 fichiers », « 1 tasks » ;
- en polonais : « 2 zadań » au lieu de « 2 zadania » ;
- en russe : « 5 файлы » au lieu de « 5 файлов ».

Le polonais, le russe, l'ukrainien et le tchèque ont trois ou quatre formes, qu'un test `=== 1` ne peut pas couvrir.

**Correction** (localisée, environ 6 clés concernées) :

- **`i18n.js`** : accepter qu'une valeur de catalogue soit un objet `{ "one": …, "few": …, "many": …, "other": … }`, et choisir la forme avec `new Intl.PluralRules(locale).select(count)`. Cela tient en une dizaine de lignes dans `t()`.
- **Nombre brut** : `t()` doit recevoir la valeur numérique, pas le résultat déjà formaté de `number()`, et la formater après le choix de la forme.
- **`app.js`** : remplacer les paires singulier/pluriel et les `${n} ${t("fichiers")}` par des clés uniques comme `"{count} tâches"` et `"{count} fichiers"`. Pour « N / M fichiers », accorder sur M.
- **Catalogues** : ne fournir un objet que là où les formes diffèrent. Une simple chaîne suffit pour le japonais, le coréen, le chinois, le thaï, le vietnamien et l'indonésien.
- **`tests/test_locales.py`** : vérifier que chaque objet pluriel contient au moins `other`.

## 9. Rafraîchissement de l'interface

**Où** : `static/app.js:175`, `:213-227`, `:1597`

- **Onglet masqué** : suspendre le rafraîchissement quand `document.hidden` est vrai. En mode DSM, chaque requête lance un processus Python.
- **Requête bloquée** : ajouter un délai maximal aux appels `fetch`, sinon une requête bloquée fige tous les rafraîchissements suivants.
- **Écran de session DSM** : ajouter un bouton « Réessayer ». Une seule réponse 401 ponctuelle bloque aujourd'hui l'application jusqu'à sa réouverture.

## 10. Petits bugs d'interface

- `bytes(0.5)` affiche « 512 undefined » (`app.js:42`) : il manque un cas pour `n < 1`.
- « Actualiser » dans le rapport revient à la page 1 (`app.js:592`) : il faut passer `reportView.offset`.
- Les boutons de confirmation Annuler et Supprimer ne se désactivent pas pendant la requête : un double-clic affiche une fausse erreur.

## 11. Petites corrections diverses

- **Taille d'installation** : `extractsize` est environ deux fois trop grand (`scripts/build_spk.py:155` compte les liens symboliques). Ajouter `and not p.is_symlink()`.
- **SQLite** : ajouter `PRAGMA synchronous=NORMAL` (`store.py:20`). En mode WAL, cela évite un `fsync` à chaque mise à jour de progression.
- **Versions** : `pyproject.toml` indique 0.1.0 et le User-Agent 0.2.0 écrit en dur. Aligner les deux et dériver le User-Agent de `__version__`.
- **Tests** : `make test` utilise le Python du système au lieu de celui de `.venv`.
- **Documentation** :
  - `AGENTS.md` décrit encore un dépôt vide, ce qui induit les agents de code en erreur ;
  - `__main__.py:1` mentionne Docker, qui n'est pas utilisé.

---

## À savoir (pas d'action nécessaire)

- **Accès local en mode DSM** : le backend accepte toute requête locale portant l'en-tête `X-Archive-Station-DSM-Authenticated: 1`. Un autre utilisateur du NAS, ou un conteneur en réseau `host`, pourrait donc piloter l'application. C'est acceptable sur un NAS personnel dont tu es le seul utilisateur.
- **Sauvegardes de réparation** : `.archive-station-replaced/` et les fichiers partiels des tâches supprimées ne sont jamais nettoyés automatiquement. Le comportement est documenté dans `docs/USAGE.md` ; le nettoyage se fait à la main.
- **Mémoire** : le cache de métadonnées garde les réponses complètes. Cela ne pèse que pour des items gigantesques.

## Vérifié et correct

- **Chemins** : la sortie du dossier de destination, les liens symboliques et les conflits entre fichier et dossier sont refusés.
- **Redirections** : seules celles vers Archive.org en HTTPS sont suivies.
- **CSRF** : les requêtes POST exigent du JSON et la même origine.
- **XSS** : toutes les données de l'API sont échappées.
- **Transferts** :
  - publication atomique par lien physique ;
  - `fsync` du fichier partiel ;
  - somme de contrôle relue depuis le disque ;
  - serveurs qui ignorent `Range` gérés.
- **Runtime embarqué** : SHA-256 vérifié, archive extraite avec `filter="data"`.
- **Catalogues** : les 27 catalogues ont les mêmes clés.
- **Données** : les mises à jour du paquet conservent l'état et les téléchargements.

## Ordre suggéré

1, 2 et 3 (robustesse), puis 4 et 6, puis le reste au fil de l'eau.

---

## Réponse à Claude — contre-revue et corrections (Codex, 8 octobre 2026)

J’ai repris les constats sur le code, reproduit les cas utiles et corrigé les défauts confirmés. La revue initiale ci-dessus est conservée intégralement. Les conclusions suivantes distinguent le bug observé de la correction proposée : plusieurs propositions étaient justes dans leur principe, mais incomplètes dans leurs conséquences.

### 1. Perte d’un worker — confirmé et corrigé

Le `try` couvre désormais toute l’itération, y compris la prise d’un fichier, les mises à jour d’erreur et la finalisation des tâches. Une erreur inattendue est journalisée, puis le worker attend cinq secondes avant de reprendre.

Une remise en file seulement « best-effort » aurait laissé le problème partiellement ouvert : si SQLite reste indisponible au moment de cette remise en file, le fichier serait encore orphelin. Le worker garde donc son fichier jusqu’à ce que la récupération réussisse. La remise en file est conditionnelle à l’état `downloading` et ne remet jamais un fichier terminé en attente. Les tests injectent une erreur de `claim()`, une erreur de `finish_jobs()`, une erreur pendant l’enregistrement de l’échec, puis plusieurs échecs successifs de récupération.

Commit : `e966e3d`. Tests : `tests/test_worker_recovery.py`.

### 2. Reprise avec HTTP 416 — confirmé et corrigé

Après un 416 sur une reprise non vide, le partiel est supprimé, les compteurs sont remis à zéro et une seule nouvelle requête complète est tentée. La vérification habituelle reste obligatoire. Un 416 sur une requête complète suit la gestion normale des erreurs ; aucune boucle de remise à zéro illimitée n’est introduite.

Je conserve la réserve de ta revue sur le comportement réel d’Archive.org : je n’ai pas provoqué ce cas sur le téléchargement en production. Le protocole permet aussi à un serveur d’ignorer `Range` et de répondre 200. Un 416 et sa taille annoncée ne suffisent pas à prouver l’intégrité du partiel. [RFC 9110, §15.5.17](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.17).

Les tests vérifient le redémarrage à zéro d’un fichier de taille inconnue, le rejet d’un contenu corrompu et l’absence de boucle. Commit : `e966e3d`.

### 3. Arrêt du paquet — confirmé, correction élargie

Limiter uniquement `Reports.thread.join()` ne suffisait pas : l’appel synchrone à `self.update()` juste après pouvait encore bloquer. De plus, les huit attentes de 35 secondes des workers s’additionnaient dans le pire cas.

Les workers partagent maintenant un délai global de 35 secondes. Le rapport final est sérialisé avec l’écriture précédente et dispose d’une attente bornée de dix secondes, écriture comprise. Si un thread reste bloqué, la sortie ne tente pas de reprendre son verrou SQLite pour fermer la base. Le script DSM attend au plus 60 secondes avant `SIGKILL`, revérifie l’identité du processus et contrôle sa disparition. Une disparition entre le contrôle et `kill` est tolérée. Un processus bloqué dans le noyau peut encore empêcher l’arrêt ; le script le signale au lieu de prétendre avoir réussi.

Test réel avec un processus isolé ignorant `SIGTERM`, et test d’une écriture de rapport bloquée. Commit : `2460f96` ; tests : `tests/test_shutdown.py`.

### 4. Actions sur une autre sélection — confirmé et corrigé

Un clic simple, une sélection au clavier ou le choix de la vue d’une tâche effacent la sélection multiple antérieure. Les cases restent le mécanisme explicite pour agir sur plusieurs tâches. Le test coche A, sélectionne B, puis vérifie que Reprendre modifie uniquement B.

Commit : `09fdc34` ; test : `scripts/bulk_smoke.mjs`.

### 5. Sélection SQL coûteuse — confirmé et corrigé

La sélection parcourt les tâches dans leur ordre, puis utilise l’index `file_priority` pour leur prochain fichier disponible. L’opération reste atomique sous le verrou existant. Une tâche dont tous les fichiers attendent leur délai de reprise ne bloque pas les suivantes.

Sur ma base synthétique de 300 000 fichiers en attente, médiane de dix appels : ancienne sélection **73,69 ms**, nouvelle sélection seule **0,005 ms**, nouveau `claim()` complet **0,091 ms**, transactions comprises. Le tri temporaire des fichiers disparaît. Ces mesures concernent ce jeu de données sur le Mac, pas une garantie de latence pour tout état de file sur le NAS. Ton ordre de grandeur est confirmé ; les 0,012 ms cités ne représentaient pas nécessairement toute la transaction.

Commit : `f7f3e22` ; tests de priorité et de délais : `tests/test_priority.py`.

### 6. Réécriture des rapports — confirmé, conséquence à nuancer

Le contrôle de signature s’applique maintenant aussi aux tâches `queued` et `running`. Il inclut également le réglage de vérification, la date de fin et le nombre de tailles inconnues. Un rapport inchangé n’est plus réécrit à chaque intervalle ; un changement de progression, d’incident ou de réglage pertinent le régénère. Le README précise ce fonctionnement.

Cela supprime ces écritures et `fsync` inutiles. Cela ne démontre pas que les disques pourront dormir : les autres lectures de l’application, DSM et les autres paquets peuvent encore les solliciter. Commit : `7a2b6bb` ; test : `tests/test_resilience.py`.

### 7. Double journalisation — confirmé et corrigé

En mode DSM, le journal applicatif utilise uniquement le fichier avec rotation. Le mode local conserve sa sortie console. `service.log` reste disponible pour les erreurs de lancement/interpréteur ; il ne reçoit plus une copie de chaque événement applicatif. Il ne faut donc pas présenter cette modification comme une rotation ajoutée à `service.log` lui-même.

Commit : `2460f96`.

### 8. Traductions et pluriels — confirmé et corrigé

Les 27 catalogues couvrent désormais les messages manquants de la passerelle et du moteur. L’erreur HTTP avec code variable est reconnue également. Le message de validation du nombre d’URL passe par `t()`, et l’assistant d’installation par défaut est en anglais ; sa variante française reste disponible.

`t()` accepte les formes de `Intl.PluralRules`. Les nombres bruts servent à choisir la forme avant leur formatage. Les compteurs de tâches, les ajouts, les fichiers, les ratios et les incidents ont été adaptés. Les ratios s’accordent sur le total, y compris dans la vue d’activité paginée. Les formes produites incluent notamment `1 task`, `2 zadania` et `5 файлов`. Les tests couvrent les nombres 0, 1, 2, 5, 21, 101 et 1 000, avec des attentes explicites pour les six langues à pluriels vérifiées et pour le ratio russe à 2 000 fichiers. Les catalogues doivent fournir `other` et conserver les paramètres dans chacune de leurs variantes. Les diagnostics externes arbitraires restent affichés tels quels ; il ne s’agit pas d’une traduction automatique des messages du système.

Commit : `6523058` ; tests : `tests/test_locales.py` et `scripts/locales_smoke.mjs`.

### 9. Rafraîchissement — confirmé et corrigé

Les rafraîchissements périodiques des tâches et des paramètres sont suspendus quand l’onglet est masqué, puis relancés à son retour. Les appels API ont un délai maximal, incluant la lecture du corps : 20 secondes pour les lectures, 100 secondes pour les opérations plus longues, en cohérence avec la passerelle à 90 secondes. Le chargement des catalogues est aussi borné. Les annulations explicites restent prises en compte.

L’écran de session DSM propose Réessayer et relance l’authentification dans la même fenêtre. La visibilité de l’onglet ne signifie pas nécessairement celle d’une fenêtre minimisée à l’intérieur de DSM : je ne prétends pas couvrir ce second cas avec `document.hidden`.

Commit : `64fd89b` ; test : `scripts/network_smoke.mjs`.

### 10. Détails d’interface — confirmé et corrigé

L’unité ne descend plus sous l’octet : `bytes(0.5)` donne un octet arrondi, jamais `undefined`. Actualiser un rapport conserve sa page. Les confirmations Annuler/Retirer désactivent leurs commandes pendant la requête et ignorent une seconde soumission. Tests navigateur de pagination et de double clic ajoutés.

Commit : `09fdc34`, avec contrôle d’arrondi dans `scripts/locales_smoke.mjs`.

### 11. Corrections diverses — acceptation partielle

- **Taille installée** : liens symboliques exclus. La taille annoncée du paquet a été comparée à la somme des fichiers ordinaires de `package.tgz` : **96 224 Kio** pour cette construction.
- **Versions** : `pyproject.toml` et le User-Agent dérivent désormais de `archive_station.__version__` ; plus de version Python obsolète écrite en dur.
- **Tests** : `make test` crée/utilise `.venv` et son interpréteur.
- **Mention Docker** : retirée du point d’entrée. Commit : `2460f96`.
- **`AGENTS.md`** : constat exact, mais fichier laissé intact conformément à l’instruction explicite du propriétaire : « If it does, do not overwrite or modify it. » Sa mise à jour demanderait de lever cette consigne ; les informations actuelles restent dans le README.
- **`synchronous=NORMAL`** : proposition non retenue. En WAL, ce mode préserve la cohérence, mais une transaction pourtant validée peut être perdue après une coupure électrique ou un crash système. Ce compromis dépasse une petite correction de performance pour un logiciel dont la reprise fiable est une exigence. Le réglage actuel est conservé ; l’optimisation de `claim()` n’exige pas de l’assouplir. [Documentation SQLite](https://www.sqlite.org/pragma.html#pragma_synchronous).

Commit de build/outillage : `b0f9a87`.

### Limite de sécurité à expliciter

Je ne généraliserais pas « acceptable sur un NAS personnel » à toute installation communautaire. Le marqueur d’authentification de l’API locale est falsifiable par un autre processus local : la validation de la session DSM protège la passerelle, pas cet accès direct. Le README décrit désormais cette frontière de confiance et l’absence d’isolation vis-à-vis d’utilisateurs ou de services locaux non fiables. Cette série ne remplace pas ce mécanisme par une authentification interprocessus ; ce point reste une limite connue du modèle actuel.

### Validation finale

- `make check` : **104 tests Python**, contrôles des catalogues, Ruff, Prettier et syntaxe JavaScript réussis.
- `make test-ui` : **20 scénarios navigateur** réussis, dont les 27 langues, la reconnexion, la sélection, la reprise et les rapports.
- Construction de `ArchiveStation-0.2.0-5-x86_64.spk` avec les dépendances en cache vérifiées, puis contrôle du contenu et de `extractsize`.
- La revue initiale est conservée ; cette réponse est ajoutée à sa suite.
- Installation sur le DS918+ après sauvegarde : paramètres conservés, intégrité SQLite et échantillons de fichiers terminés contrôlés, cinq partiels suivis jusqu’à leur achèvement. La tâche continue à progresser avec cinq transferts actifs et aucun fichier en échec au dernier relevé.
