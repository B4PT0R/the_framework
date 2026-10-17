# Refonte et harmonisation — backlog

Relevé du 10 octobre 2026. The Harness est le cas canonique, mais une extraction
n'est justifiée que si elle sert aussi d'autres applications et réduit la surface
conceptuelle totale. Ces notes ne constituent pas des demandes de refonte immédiate.

## Livré dans cette passe

- [x] `CHAT` partagé : le starter et The Harness utilisent les mêmes routes texte,
  pièces jointes et barrière texte/voix, au lieu de reconstruire cette plomberie.
- [x] Contributions de contexte vocal : mémoire et registre possèdent leurs
  producteurs ; le transport les assemble et garantit leur nettoyage.
- [x] Vérifier le framework et le starter indépendamment des imports de produit.

## Priorité : contrats et frontières

- [ ] **Artefacts comme items canoniques de conversation — proposition du 17 octobre.**
  Baptiste propose `Artifact(type="artifact", kind="image" | "file" | "widget",
  payload={...})`, identifié et persisté par le worker dans la session. Même
  item pour événement live et reprise paginée, sans journal de replay UI séparé.
  Projection UI : description JSON rendue selon le kind. Projection modèle :
  message developer indiquant sa présence dans le flux, avec identité et
  métadonnées utiles au feedback ; aucune affirmation implicite de rendu réussi.
  À intégrer aux sorties d'outils/commandes, au décodage des ResponseItem, aux
  projections de session, à la compaction et aux clients starter/Harness.
  Le stockage durable des fichiers référencés reste nécessaire : réutiliser les
  mécanismes de fichiers, éviter les chemins temporaires et URLs éphémères.
  Contrat explicite : `Image` fournit une entrée vision à l'agent et ne demande
  aucun affichage dans le chat. Montrer une image à l'utilisateur exige un
  `Artifact(kind="image", ...)`. La projection UI doit exclure les `Image`
  bruts, y compris captures d'inspection et résultats d'outils de vision.
  Un même fichier peut être référencé par les deux items si l'agent veut à la
  fois le voir et le montrer ; ces intentions restent indépendantes.
  Widgets : description sérialisable et renderer applicatif ; reprise historique
  à partir de l'item, avec fallback lisible si son kind n'est pas supporté.
  Vérifier ordre, absence de doublons, reload/reconnexion/restart et compaction.
  Proposition consignée, pas encore implémentée.

- [ ] **Activation des plugins pendant la voix — à examiner.**
  `plugins/realtime/api.py`, `plugins/memory/live.py`, `server/composition/plugins.py`.
  Le chargement du runtime, l'exposition à l'agent et la présence d'un contexte
  vocal sont trois états distincts. Vérifier qu'une désactivation mémoire/registre
  pendant un appel purge ou bloque correctement les projections déjà préparées.
  Validation : désactivation/réactivation pendant une réponse ou une délégation,
  sans fuite de contexte ni perte de la conversation canonique.
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

## Priorité : assemblage public et cycle de vie

- [x] **Point d'assemblage conversationnel unique — livré le 17 octobre.**
  `ApplicationSpec.build_conversation` construit superviseur, runtime, API,
  santé et transports. Starter et Harness migrés ; projections, politiques
  d'accès, resets et runtime spécialisé restent applicatifs. Le serveur Harness
  ne reconstruit plus son superviseur. Tests avec vrais workers : même session
  et historique après reconstruction, texte seul et voix installée.
- [ ] **Bootstrap desktop persistant — duplication observée.**
  `starter/desktop.py`, `server/runtime/discovery.py`, `server/clients/browser_rpc.py`.
  Comparer au serveur/navigateur indépendants de The Harness. Protéger la continuité
  du navigateur et l'écoute locale pendant le remplacement du serveur. Identifier
  une éventuelle primitive de cycle de vie, pas une succession de wrappers.
- [ ] **Surface éditable — contrat de livraison à clarifier.**
  `server/composition/application.py`, `server/clients/surfaces.py`,
  `server/composition/controls.py`.
  Distinguer build initial, artefact de secours, aperçu authentifié, version publiée
  et rafraîchissement. Montrer dans les exemples une seule voie canonique.
  Validation : premier démarrage, build échoué, rollback, restart avec UI connectée.
- [ ] **Clients distants — extraction éventuelle, pas automatique.**
  `server/clients/remote.py`, `server/clients/tailscale.py` et le plugin distant du
  produit. Le transport générique existe déjà ; la composition produit reste
  volumineuse. Faire l'inventaire des policies, projections et contributions avant
  de décider s'il manque réellement une feature déclarative côté bibliothèque.
  Ne pas déplacer en bloc les protocoles ou choix de desktop/VR du produit.

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
