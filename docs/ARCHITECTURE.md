# Architecture

Archive Station est une application Python légère avec une interface statique, sans chaîne de compilation frontend. Waitress sert l’API WSGI. SQLite conserve les tâches et chaque fichier ; les paramètres sont stockés séparément. DSM gère la connexion au paquet.

## Flux de téléchargement

1. `ArchiveClient` extrait un identifiant strictement validé de l’URL et interroge l’API officielle `https://archive.org/metadata/{identifier}`.
2. L’analyse applique les options de sélection, refuse les chemins dangereux et décrit le nombre de fichiers, leur volume et les éventuels fichiers privés exclus. Un cache de quatre manifestes au maximum évite de répéter immédiatement l’analyse.
3. Une transaction SQLite enregistre la tâche, sa destination propre et ses fichiers. Un même identifiant ne peut avoir qu’une tâche présente dans la liste.
4. Un ensemble borné de workers réclame les fichiers de manière atomique. Les paramètres limitent le nombre actif ; les tâches sont servies dans l’ordre d’ajout.
5. Chaque worker écrit dans un fichier partiel, reprend avec `Range`, contrôle la taille et, si disponible, l’empreinte. La publication finale utilise un lien physique atomique sans écrasement, sur le même système de fichiers.
6. La vue Activité interroge un aperçu borné : fichiers actifs, cinq erreurs au maximum et dix prochains fichiers, dans l’ordre de la file de téléchargement. Elle traverse tous les sous-dossiers sans pagination. Les vues Terminés et À vérifier utilisent des listes filtrées paginées ; la vue Arborescence ne charge que les niveaux ouverts. Un changement de vue ignore les réponses devenues obsolètes.

Les tailles inconnues ne permettent pas une estimation exacte avant transfert. Les sommes de contrôle sont utilisées comme vérification d’intégrité, pas comme mécanisme cryptographique d’authentification. Les fichiers de métadonnées portant `summation` ne sont pas validés contre cette valeur, qui décrit leurs entrées.

Le temps restant utilise les octets réellement écrits par les workers, regroupés par seconde et par tâche sur cinq minutes (301 cases au maximum, précision d’une seconde). L’historique est en mémoire sur le NAS : aucune écriture SQLite par bloc ni fenêtre ouverte n’est nécessaire. Les fichiers déjà présents et les octets partiels repris ne gonflent pas le débit. Le calcul commence après trente secondes ; une minute sans données masque la durée. Pause, reprise et redémarrage réinitialisent la moyenne. Les tailles inconnues donnent une estimation minimale ; leurs octets reçus ne sont pas soustraits du volume connu restant. Une erreur définitive suspend l’estimation jusqu’à la nouvelle tentative.

## Contrôles d’accès

Les requêtes vers Archive.org acceptent uniquement les URL d’éléments valides et les redirections HTTPS vers ses domaines. Les chemins absolus, traversées `..`, composants vides et liens symboliques préexistants sont refusés. Les destinations sont limitées aux racines autorisées et aux permissions du processus.

En développement autonome uniquement, l’application conserve un mot de passe PBKDF2-HMAC-SHA256 à sel aléatoire, avec 600 000 itérations. Les sessions sont en mémoire, expirent après 24 h et sont invalidées au redémarrage ou au changement de mot de passe. Les modifications exigent du JSON et une origine compatible. Aucune politique CORS permissive n’est activée.

Sur DSM, le serveur écoute seulement `127.0.0.1:8274`. Une passerelle CGI de taille limitée relaie ses seules routes `/api/`, sous l’adresse DSM. La passerelle valide la session auprès du programme officiel `authenticate.cgi` et exige le groupe `administrators`. Le serveur en mode `--dsm-auth` accepte uniquement les requêtes locales portant le marqueur de la passerelle. Les cookies et jetons DSM ne sont jamais transmis au backend. Aucun mot de passe supplémentaire n’est demandé. Les permissions de partage sont accordées à l’utilisateur système du paquet, sans exécution du service en root.

Les racines de destination doivent être des dossiers de confiance : la vérification des liens symboliques n’est pas destinée à isoler un processus local malveillant capable de modifier simultanément leur arborescence.

## Références

- [API de métadonnées Internet Archive](https://archive.org/developers/md-read.html)
- [Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/)
- [Runtime Python autonome](https://gregoryszorc.com/docs/python-build-standalone/main/running.html)

## Rapports et langues

Un thread distinct écrit toutes les quinze secondes un rapport texte `.txt` dans le dossier de chaque élément, par remplacement atomique. Ses erreurs sont journalisées sans bloquer les transferts. Le schéma SQLite est migré en place pour conserver la source saisie et la date de fin ; les anciennes tâches restent utilisables. La durée du rapport comprend les pauses et les arrêts du service.

Les 27 catalogues JSON sont embarqués. Le choix manuel est enregistré avec les paramètres. En mode automatique, la langue de la session DSM précède celle de la configuration serveur et du navigateur ; l’anglais sert de repli. Aucun service de traduction n’est appelé par l’application. Voir `docs/TRANSLATIONS.md`.
