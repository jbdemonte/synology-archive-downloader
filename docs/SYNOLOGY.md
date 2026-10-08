# Paquet DSM

## Construction

`make build` crée `dist/ArchiveStation-<version>-x86_64.spk` et sa somme SHA-256. Le paquet contient Python 3.12 Linux x86_64, Waitress, l’application, l’interface DSM et les licences. Le NAS n’effectue aucun téléchargement de dépendance lors de l’installation.

Le runtime est verrouillé dans `runtime-lock.json`. Les textes de licence dans `runtime-licenses/` proviennent de l’archive complète de la même distribution ; `PROVENANCE.txt` en consigne la somme SHA-256. Les fichiers `terminfo` ne sont pas inclus : le service n’utilise pas de terminal, et certains alias de cette base sont incompatibles avec un système de fichiers macOS insensible à la casse.

## Intégration à DSM

- Identifiant du paquet et utilisateur système : `ArchiveStation`.
- Identifiant de l’application : `com.archivestation.app`.
- `dsmuidir="ui"` enregistre l’icône et le lanceur de fenêtre DSM.
- Le lanceur `legacy` ouvre `ui/web/index.html` dans une iframe du bureau DSM 7.1. Les ressources de l’application restent dans `ui/web/`. DSM charge automatiquement `ui/style.css` dans le bureau : ce fichier doit rester vide de règles CSS.
- Les ressources statiques et `gateway.cgi` utilisent `/webman/3rdparty/ArchiveStation/`.
- La passerelle conserve l’origine DSM et relaie les appels au port local 8274. Il n’est pas nécessaire d’ouvrir un port supplémentaire ni de modifier Nginx.
- `conf/privilege` abaisse les droits à l’utilisateur du paquet.
- Le worker officiel `data-share` crée le partage `ArchiveStation` et lui accorde l’accès. Les autres partages nécessitent une permission explicite dans DSM.

## Connexion unique DSM

Aucun mot de passe propre au paquet n’est demandé. À chaque appel API, `gateway.cgi` exécute le programme officiel `/usr/syno/synoman/webman/modules/authenticate.cgi` avec l’environnement CGI original. Seuls les utilisateurs authentifiés du groupe `administrators` ont accès, conformément au lanceur `allUsers: false`. Une session absente ou expirée reçoit une réponse 401 ; un compte sans autorisation reçoit une erreur logique 403.

DSM remplace les réponses HTTP 403, 404 et certaines erreurs serveur par une page HTML. Pour conserver les messages de l’API, la passerelle transporte ces erreurs en HTTP 200 avec leur code dans `_http_status` ; le frontend les traite toujours comme des échecs. Les contrôles d’authentification et de permissions restent obligatoires. Aucun réglage Nginx n’est modifié.

Le lanceur et les ressources statiques portent la version du paquet dans leur URL pour renouveler le cache après une mise à jour. La fenêtre s’ouvre par défaut en 1360 × 840 ; la mise en page s’adapte également aux fenêtres plus petites, avec défilement à l’intérieur de la liste de fichiers.

Le serveur démarre avec `--dsm-auth` sur la boucle locale et exige le marqueur ajouté par la passerelle après validation. Les cookies DSM ne sont pas transmis au backend. Les modifications restent soumises aux vérifications d’origine et de format JSON. Les anciens fichiers de mot de passe d’Archive Station sont ignorés dans ce mode.

Après la mise à jour depuis les premières versions, recharger complètement la page DSM pour retirer les anciens styles déjà chargés dans le navigateur. Aucun fichier du Centre de paquets n’est modifié.

## Validation avant distribution

Le build et `synopkg query` valident la structure, mais ne prouvent pas à eux seuls le fonctionnement du lanceur. Tester sur un NAS : installation, lancement dans DSM HTTP et HTTPS, choix d’un partage, petit transfert, pause/reprise, redémarrage et mise à jour avec conservation des données. Documenter séparément les architectures et versions DSM effectivement testées.

La désinstallation arrête le service. Le partage et les téléchargements ne sont pas supprimés ; les données persistantes DSM ne sont pas effacées par les scripts du paquet. Aucun changement n’est apporté aux autres paquets.

## Sources

- [Structure d’un paquet Synology](https://help.synology.com/developer-guide/synology_package/introduction.html)
- [Permissions des paquets](https://help.synology.com/developer-guide/privilege/privilege_config.html)
- [Création d’un partage de données](https://help.synology.com/developer-guide/resource_acquisition/data_share.html)
- [Stockage persistant DSM](https://help.synology.com/developer-guide/integrate_dsm/fhs.html)

- [Authentification des applications DSM](https://help.synology.com/developer-guide/integrate_dsm/web_authentication.html)
