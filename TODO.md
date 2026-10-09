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

- [ ] **Activation des plugins pendant la voix — à examiner.**
  `plugins/realtime/api.py`, `plugins/memory/live.py`, `server/composition/plugins.py`.
  Le chargement du runtime, l'exposition à l'agent et la présence d'un contexte
  vocal sont trois états distincts. Vérifier qu'une désactivation mémoire/registre
  pendant un appel purge ou bloque correctement les projections déjà préparées.
  Validation : désactivation/réactivation pendant une réponse ou une délégation,
  sans fuite de contexte ni perte de la conversation canonique.
- [ ] **Assemblage à contexte mutable — simplification possible.**
  `server/composition/application.py:BuildContext`, `plugins/realtime/server.py`.
  Les contributions sont déjà déclarées, mais certaines capacités installent des
  callbacks dans le contexte pendant la construction des services. Documenter les
  étapes où ce contexte est mutable et tester l'indépendance à l'ordre des plugins.
  N'ajouter une primitive que si elle remplace plusieurs couplages concrets.
- [ ] **Sécurité locale et transport — comparaison à mener.**
  `starter/security.py`, `server/api/websockets.py`, `server/clients/surfaces.py`.
  Le starter possède une politique stricte Host/Origin, tandis que le produit a
  une autre politique et un middleware HTTP. Définir d'abord leurs invariants
  communs ; ne pas ajouter un « security manager » de plus. Tester cookie,
  bearer, CSRF, DNS rebinding, aperçu de surface et échanges WebSocket.

## Priorité : assemblage public et cycle de vie

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
