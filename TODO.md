# Refonte et harmonisation — backlog

Relevé du 10 octobre 2026. The Harness est le cas canonique, mais une extraction
n'est justifiée que si elle sert aussi d'autres applications et réduit la surface
conceptuelle totale. Ces notes ne constituent pas des demandes de refonte immédiate.

## Livré dans cette passe

- [x] **Chat intégré à l’hôte — 18 octobre.** `build_conversation()` assemble les
  routes texte/pièces jointes et la barrière texte/voix. Plugin `CHAT` et capacité
  artificielle supprimés ; transports locaux/distants injectent le service `chat`.
  Starter et Harness partagent ce socle, absent du catalogue des plugins.
  Validation : 642 tests Framework, 3 skips, 18 tests JS et build ; scénario
  Chromium réel avec reprise, pièces jointes, aperçu, publication et rollback.
  Harness : 151 tests, 46 tests JS, build et installation locale vérifiée.
- [x] Contributions de contexte vocal : mémoire et registre possèdent leurs
  producteurs ; le transport les assemble et garantit leur nettoyage.
- [x] Vérifier le framework et le starter indépendamment des imports de produit.

- [x] Bucket client unique `data_root/bucket/` pour uploads et publications,
  sans distinction image/vidéo/audio/fichier. Route générique authentifiée,
  sous-dossiers et byte ranges ; pas de migration des données expérimentales,
  explicitement jetables pour Baptiste. Références et métadonnées seules en session.

- [x] Suivi de conversation du starter synchronisé avant le rendu via un effet
  de layout. Évite qu'un chargement d'image fasse perdre le suivi automatique ;
  même correction et régressions Chromium dans le Harness.

## Priorité : contrats et frontières

- [x] **Artefacts intégrés — 18 octobre.** `Artifact(kind, payload)` est un item
  natif : outils et commandes le rendent durable via la session canonique,
  les providers le gardent éphémère pendant le tour courant, sans écrire leurs
  sorties dans la session. Le modèle reçoit une notice developer avec le JSON
  et la portée exacte, sans affirmation de rendu client. Événement de remplacement
  pour les vues temporaires, nettoyées en fin de tour ou perte de connexion.
  `Image` reste exclusivement une entrée vision, exclue du chat.
  Plugin complet `ARTIFACTS` : outil facultatif de présentation, copie atomique
  des fichiers et route authentifiée pour leurs octets, sans journal de replay.
  Starter et Harness : images/fichiers, renderers applicatifs par kind, repli
  pour kind inconnu ou renderer en erreur. Tests de reprise/compaction, doublons,
  fichiers sources supprimés et UI Chromium. Les widgets ne sont pas du code
  fourni par le modèle : leur comportement appartient au renderer applicatif.
  Validation : 612 tests Framework, 4 skips ; scénario starter Chromium réel
  avec image conservée malgré suppression du fichier source ; 18 tests JS et build.

- [x] **Bindings pendant la voix — cohérence validée le 18 octobre.** Un plugin
  est une tranche d'application ; son installation et son binding sont distincts.
  Ces changements rares ferment/recréent l'appel, avec snapshot canonique actuel,
  plutôt qu'un retrait de contexte déjà transmis. Reprise après barrière worker,
  sans interblocage depuis un outil délégué. Producteurs mémoire/registre limités
  aux providers autorisés ; fermeture des tâches/caches et files d'attente.
  Arrêt manuel prioritaire, IDs d'appel corrélés, désactivation voix sans relance,
  erreurs de réconciliation et arrêt serveur couverts. Écriture des dernières
  transcriptions dans le worker canonique, indépendante du binding voix. Validation : 654 tests
  Framework, 153 Harness réussis et un échec intermittent de suivi d’image
  traité ensuite ; 25/53 tests JS, builds et navigateur avec audio simulé réussis.
  Le test backend vocal réel reste opt-in ; aucune inférence pour cette validation.
- [x] **Assemblage à contexte mutable — clarifié le 17 octobre.**
  `server/composition/application.py:BuildContext`, `plugins/realtime/server.py`.
  Entrées fixes et options par plugin ; `provide` retiré. Les services s'injectent
  par dépendances et sont consultables en lecture seule après démarrage. Les
  contributions sont scellées avant les fabriques de services, y compris la
  coordination des changements de binding pendant la voix. Tests d'ordre des
  plugins, de cycle de vie et de refus des contributions tardives.
- [ ] **Sécurité locale et transport — comparaison à mener.**
  `starter/security.py`, `server/api/websockets.py`, `server/clients/surfaces.py`.
  Le starter possède une politique stricte Host/Origin, tandis que le produit a
  une autre politique et un middleware HTTP. Définir d'abord leurs invariants
  communs ; ne pas ajouter un « security manager » de plus. Tester cookie,
  bearer, CSRF, DNS rebinding, aperçu de surface et échanges WebSocket.
  Aperçus livrés le 18 octobre : authentification à ticket unique, cookie signé
  persistant et rôle lecture seule partagés ; garde Host/Origin applicative conservée.
  Outil `system.preview_surface`, événement primaire exclusif, aperçus concurrents
  et bridge refusé aux aperçus. Starter/Harness adaptés et validés en Chromium réel.

- [x] **CLI de bootstrap complète — 18 octobre.** `bootstrap` crée et prépare,
  `prepare` reprend une installation interrompue, `run` prépare si besoin puis
  démarre. Projet Python/venv propre à l'application, Chromium, npm et build ;
  caches par étapes avec reconstruction après édition de l'UI. Copie seule via
  `--no-prepare`, lancement immédiat via `--run`. Validation : 621 tests Framework
  et démarrage réel d'une application hors dépôt, dans un chemin avec espaces,
  avec Python isolé, Chromium connecté et réutilisation du build au second lancement.

## Priorité : assemblage public et cycle de vie

- [x] **Point d'assemblage conversationnel unique — livré le 17 octobre.**
  `ApplicationSpec.build_conversation` construit superviseur, runtime, API,
  santé et transports. Starter et Harness migrés ; projections, politiques
  d'accès, resets et runtime spécialisé restent applicatifs. Le serveur Harness
  ne reconstruit plus son superviseur. Tests avec vrais workers : même session
  et historique après reconstruction, texte seul et voix installée.
- [x] **Cycle de vie desktop commun — 18 octobre.** `LocalServer` possède le
  verrou d'instance, le socket local, la découverte, les credentials et l'arrêt
  Uvicorn ; starter et Harness l'utilisent sans dupliquer ces responsabilités.
  Le starter conserve son superviseur et son socket parent ; systemd conserve
  la supervision du Harness sur un port fixe. Navigateur indépendant, clé cookie
  persistante et bearer renouvelé par processus. Contexte RPC propriétaire de
  Chromium, abonnements aux signaux bornés et surveillance parent sans thread.
  Readiness du starter liée au PID attendu et à un worker sain. Tests de reprise
  avec vrais processus/Chromium et carte des propriétaires dans docs/framework.md.
- [x] **Surfaces éditables — livraison clarifiée le 18 octobre.** Initialisation
  dans le lifespan du service, indépendante du runtime agent ; artefact initial
  déduit de chaque déclaration, avec bundle main externe facultatif. Version
  publiée prioritaire, repli vers la précédente si ses fichiers ont disparu,
  copies complètes avant exposition et candidat persisté sans publication au
  restart. `system.surfaces` partage la vue d'état HTTP ; build/publication/rollback
  restent des outils agentiques, sans contrôles dans l'UI de base. Scénario réel
  starter avec restart avant publication ; initialisation et versions indépendantes
  desktop/mobile/Quest couvertes par test. Clients natifs toujours à développer.
- [ ] **Clients distants — extraction éventuelle, pas automatique.**
  `server/clients/remote.py`, `server/clients/tailscale.py` et le plugin distant du
  produit. Le transport générique existe déjà ; la composition produit reste
  volumineuse. Faire l'inventaire des policies, projections et contributions avant
  de décider s'il manque réellement une feature déclarative côté bibliothèque.
  Ne pas déplacer en bloc les protocoles ou choix de desktop/VR du produit.
  Prendre en compte plusieurs surfaces clientes et types de device. Le bridge
  local `/api/v1/application` reste exclusif (seconde socket refusée en 1013),
  comme la connexion primaire `/api/v1/events` ; seuls les aperçus authentifiés
  ont des flux concurrents. Distinguer cette autorité locale des
  clients distants multiples avant d'adapter le routage des capacités.

## Documentation et validations ultérieures

- [ ] Écrire une carte courte des propriétaires : agent, worker, serveur, plugin,
  surface, navigateur. Chaque état mutable doit avoir un propriétaire évident.
- [ ] Ajouter une matrice d'assemblage : texte seul ; voix sans mémoire ; mémoire
  sans voix ; outils sans UI ; navigateur persistant. Ne pas réduire les garanties
  à « le produit démarre ».
- [ ] Tests réels voix/authentification et reprise navigateur : les suites par
  défaut n'exécutent pas tous les scénarios nécessitant un compte ou des capacités
  backend. Garder les tests isolés et les tests live clairement distingués.
- [ ] Préparer version et notes de migration uniquement après stabilisation ;
  aucune publication ou modification de dépendances externes implicite.

- [x] **Suivi des images — course scroll/load corrigée le 18 octobre.** Le starter
  et le Harness distinguent déplacement vers le haut et croissance du contenu.
  Un scroll livré avant le load ne désactive plus le suivi. Régression durable
  rouge/verte dans le Harness ; 155 tests Harness, 53/25 JS, builds et scénario
  Chromium de récupération du starter réussis.
