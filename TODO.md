# Refonte et harmonisation — backlog

Relevé du 10 octobre 2026. The Harness est le cas canonique, mais une extraction
n'est justifiée que si elle sert aussi d'autres applications et réduit la surface
conceptuelle totale. Ces notes ne constituent pas des demandes de refonte immédiate.

## Priorité après la tranche sessions — remarque du 18 octobre

- [x] Définir un vocabulaire d'événements Framework complet et délimité pour les
  consommateurs. Convertir ou filtrer les événements Responses reçus en interne,
  au lieu de transmettre les signaux backend verbatim. S'appuyer sur LocalEvent
  et enrichir les événements Framework. Reprendre après validation de la tranche
  sessions ; cette remarque ne change pas la tâche en cours.
  Livré : conversion Responses et Live, types/attributs bornés dans les enveloppes,
  séparation flux provisoire/commit canonique, texte/raisonnement/outils, état et
  transcriptions vocales. Événements propres à Realtime déclarés dans le plugin,
  sans branche métier dans le worker. Starter/Harness/CLI/clients distants et
  délégations adaptés, anciennes classes Responses et fallback brut supprimés.
  Validation : suite Framework 675 succès, 3 skips ; 18 contrats de frontière
  (deux ajoutés après cette suite), reprise Chromium starter vérifiée ; suite
  Harness 156 succès et scénario visuel supplémentaire réussi, JS 54/28 et builds.
  Serveur installé redémarré et candidat publié : même identifiant de session,
  historique/archives JSON inchangés, desktop existant retrouvé. Aucun chemin de
  migration ou compatibilité ajouté pour ces événements éphémères.

## Revue d'ensemble avant refonte de l'API — 18 octobre 2026

### Critère de conception confirmé par Baptiste

Dessiner d'abord l'API que l'on aimerait utiliser, puis confronter cette forme
aux responsabilités et aux garanties existantes. Le découpage actuel n'impose
pas les noms ni le nombre d'objets de la cible.

- Concevoir pour un humain découvrant la bibliothèque et pour un agent qui doit
  l'assimiler rapidement : mêmes termes dans le code, les exemples et la référence.
- Point d'entrée Application, responsabilités explicites et mécaniques homogènes ;
  un nom doit permettre de deviner ce que l'objet représente et ce qu'il possède.
  Ne pas imposer Spec/Context/Runtime comme vocabulaire public par héritage du code.
- Partir d'un exemple minimal complet de création/lancement, puis l'étendre avec
  plugins, sessions concurrentes et surfaces clientes sans changer de modèle mental.
  Peu de concepts, classes utiles et complexité avancée révélée progressivement.
- Rendre lisibles les effets de l'assemblage : déclaration ou instance vivante,
  moment de la construction et du démarrage, propriétaire des ressources,
  portée application/session, persistance, échec et arrêt. Éviter la magie implicite
  autant que les paramètres purement techniques imposés au démarrage simple.
- Livrer une documentation détaillée et cohérente : démarrage réellement exécutable,
  carte des responsabilités, exemples progressifs, référence des options/défauts et
  des effets de cycle de vie. Un nouvel utilisateur ne doit pas reconstruire
  l'historique du Framework pour comprendre un assemblage ou le modifier.
- Évaluer la cible sur le starter et le Harness avant de figer les noms ; préserver
  la continuité texte/voix et les scopes multi-session. Les modèles de payloads
  restent exclus du refactor. Pas de couches de compatibilité expérimentales.

### Première tranche API livrée — Application et propriétaires

Décision cible actualisée de Baptiste : un singleton Application global au
processus serveur, l'application étant essentiellement un serveur unique. Cela
remplace le choix initial de déclaration réutilisable construisant plusieurs hôtes
et l'avis précédent de ne pas introduire de singleton.

- [x] Configuration et ressources réunies dans Application ; ApplicationHost et
  les copies d'Application par session supprimés. Application.current() expose
  uniquement l'application en fonctionnement, sous contrôle du lifespan (y compris
  échec, annulation et démarrages concurrents). BuildContext.application référence
  ce même propriétaire ; SessionRuntime garde services et ressources de session.
  Les déclarations server_extensions restent distinctes des instances extensions,
  et access_policy porte la politique effective de construction.
- Validation : 699 tests Framework réussis, 3 skips ; 157 Harness. Starter/wheel,
  isolation des sessions et coordination texte/voix couverts. Installation relancée
  sans changer le processus navigateur ni la conversation canonique ; chat reconnecté,
  historique et contrôles retrouvés, aperçu authentifié ouvert puis fermé.
  application.connected mesure le bridge de capacités, pas la connexion du chat.
- Conserver un import inerte pour les workers, un démarrage/arrêt explicite et la
  reprise après restart. Le singleton du serveur n'est pas une mémoire Python
  partagée entre processus ; un import worker ne crée pas un autre serveur actif.
- Plusieurs sessions et clients restent permis. Leurs workers, réglages et routages
  restent propres à chaque session : pas de pointeur global current_session.

État livré avant cette décision (à faire évoluer, sans couche de compatibilité) :

- `Application` remplace ApplicationSpec. `build(reference, context, ...)` intègre
  la conversation ; build_conversation et le helper public build_application sont
  retirés, sans alias. Le constructeur/compile ne démarrent rien ; le lifespan
  ASGI démarre et arrête les ressources construites.
- Une définition sans primary_agent construit explicitement des services/surfaces
  sans conversation. Une définition avec agent exige sa référence importable et
  ses entrées de construction : pas de fallback silencieux vers un serveur vide.
- L'hôte expose definition (source réutilisable), security (politique effective),
  primary_session et les opérations open/list/close_session. SessionRuntime vit
  dans server.runtime.session ; le client agent dans agent.runtime.bridge.
- session_factory et session_extensions sont invoqués par session, contrairement
  aux services de portée application partagés. Le Harness personnalise ce pilote
  via HarnessSession. Le nom de service interne runtime et les protocoles clients
  restent inchangés. Aucune migration ou modification des modèles de payloads.
- Starter, Harness, exemples et documentation adaptés ensemble. Les factories
  servent à la construction et à l'injection ; les instances fournies directement
  ne sont pas clonées implicitement. La garantie de plusieurs hôtes simultanés
  retirée des tests généraux ; les tests de démarrage différé, arrêt/reprise,
  import worker et isolation des sessions sont conservés.
- Validation : 694 tests Framework réussis (3 skips), 157 Harness, Ruff critique
  et diff vérifiés. Démarrage différé et absence de conversation implicite ;
  wheel/starter, sessions et texte/voix couverts. Après précision du périmètre
  mono-application : assertions multi-hôtes retirées, 50 tests ciblés réussis ;
  les garanties de reprise et d'isolation des sessions restent couvertes.
  Installation redémarrée : JSON de session strictement identique, même PID du
  navigateur, catalogue de plugins et aperçu authentifié fonctionnels.

### Tranche plugins / construction livrée — 18 octobre

- Une factory de service reçoit explicitement `context` (BuildContext) et ses
  dépendances nommées ; plus besoin d'une factory de plugin qui fabrique une
  closure de construction pour lire chemins et options. `context` n'est pas une
  dépendance de service et n'est jamais injecté implicitement dans **kwargs.
- Les références service_factory="module:factory" restent inertes au compile/import
  worker ; résolution et inférence des dépendances précèdent le DAG de construction.
- Même découverte des routes décorées et sockets pour service direct et factory.
  Les flags "service" et with_endpoints inutilisé sont retirés ; None découvre,
  () masque explicitement. Authentification et validation restent obligatoires.
- Scheduler, système, artefacts et services Harness aplatis ; starter utilise les
  chemins par défaut des plugins. Builders conservés pour contributions avant
  construction (voix, mémoire, registre, politiques distantes), pas de nouvelle
  classe publique ni helper d'assemblage. Payloads et protocoles inchangés.
- Validation : suite complète Framework 704 succès / 3 skips avec Chromium,
  puis 6 tests ciblés de construction (dont **kwargs nommé context) ; Harness 157.
  Isolation des contextes par session, échecs optionnels, coordination voix/reset,
  wheel/starter et reprise réelle du navigateur couverts. Installation relancée :
  même navigateur et conversation, contrôles retrouvés, catalogue et aperçu vérifiés.
- Tests agentic du starter : assertions sur les anciens chemins de factories
  supprimées ; inventaire courant conservé sans verrouiller les adaptateurs internes.

### Revue Session / Context / Compaction — 18 octobre

Revue effectuée, propositions ci-dessous non implémentées. Sources :
agent/context/{session,builder,compaction,projections}.py, runtime/{agent,
agentic_loop,worker}.py, projections Realtime, recettes starter/Harness et tests.

À conserver :
- Session : état canonique, historique actif et archive, configuration/état/bindings,
  reçus de commandes, mutations atomiques et reprise. Ne pas créer SessionData +
  SessionStore + SessionManager pour répartir artificiellement ces responsabilités.
- Context : vue de travail de l'agent, préfixes dynamiques, providers éphémères,
  budget local et préparation de l'entrée du modèle. Ce n'est pas BuildContext.
- Projections clientes : sélection de l'archive par tours puis projection pure
  nommée dans Worker/SessionProjections. Realtime garde sa projection propre ;
  ne pas imposer une abstraction commune aux formats Responses, voix et UI.
- Les modèles Compaction, CompactionResponse, SessionWatermark et autres payloads
  restent hors refonte. Deux Compaction existent aujourd'hui : un item de modèle
  et un contrôleur runtime ; seule la nécessité du second est remise en question.

Priorités proposées, sans nouvelle classe/helper public :
1. Déplacer la conversion de séquences vers Responses de Session.to_api_format
   vers Context. Génération et compaction doivent partager la préparation du
   contexte canonique ; seule la génération ajoute les providers éphémères.
   Garder Image/Artifact.to_api_format et les autres logiques locales de payloads.
   Rendre explicite la réparation canonique avant projection : aujourd'hui une
   méthode nommée comme une conversion peut modifier/persister la session.
2. Ramener l'orchestration de compaction dans Context, garder agent.compact()
   comme opération usuelle et Session.commit_compaction() comme mutation atomique.
   Supprimer le contrôleur Compaction(agent), agent.compaction et le fallback
   getattr(agent, "context", None), plutôt que les remplacer par un autre manager.
3. Retirer les branches/options sans usage utile : compose_session_items vérifie
   une ancre puis concatène prefix + history[:tail_start] + history[tail_start:],
   exactement comme son retour général prefix + history. L'option compact(input=...)
   n'a qu'un usage de test dans les consommateurs explorés ; elle permet pourtant
   de remplacer le contexte canonique à partir d'une autre séquence. Préférer une
   opération compactant explicitement la session courante ; adapter le test de clé
   de cache au contrat réel. Ne pas supprimer tail_start du schéma à cette occasion.

Garanties à vérifier lors de l'implémentation : préfixe avant toute sortie compactée,
limites d'images et feedback d'artefacts, providers non persistés/non compactés,
archive restituable malgré réduction du contexte actif, appels d'outils interrompus
réparés sans répétition de leurs effets, échec de sauvegarde sans mutation partielle,
usage mesuré distinct du comptage local, événements publics inchangés. La commande
manuelle de compaction recalcule déjà le budget dans Worker : pas de correction
supplémentaire nécessaire sur la base de cette revue.

Impact consommateur : ni le starter ni le Harness n'assemblent Context/Compaction
ou n'appellent Session.to_api_format directement ; ils déclarent la projection
"display" et passent par les commandes du worker. Adapter en priorité les appels
Framework et tests, puis vérifier le couple complet et la reprise desktop.
Validation de la revue : 44 tests Session/contexte/compaction/artefacts réussis.
Aucun changement runtime, modèle, schéma, installation ou publication effectué.

### Secteurs à revoir

- Minimiser le vocabulaire nécessaire aux cas simples et réutiliser les mêmes
  concepts pour les cas avancés. Préférer des classes cohérentes portant données,
  invariants et opérations utiles ; éviter les helpers d'assemblage parallèles,
  les classes de simple transmission et les objets géants à dizaines d'options.
- Distinguer les payloads `modict` avec logique locale, les déclarations de
  construction et les objets runtime propriétaires de ressources/cycles de vie.
  Le nombre de types de protocole n'est pas une mesure de complexité de l'API.
  L'héritage `modict` seul ne classe pas un objet : Session et BrowserSession
  mêlent actuellement données et responsabilités runtime.
- [x] Clarifier les propriétaires : Application porte configuration et ressources
  globales ; SessionRuntime pilote une session. ApplicationHost a été supprimé.
  Les canaux d'appels sont nommés bridge côté agent et serveur ; les payloads
  conservent leurs noms/schémas.
- [x] Revoir ensemble Plugin, AgentExtension, ServerExtension et BuildContext :
  injection directe du contexte, construction différée et découverte unifiées ;
  les contributions avancées gardent leur phase explicite avant les services.
- [x] Examiner les frontières Session / Context / Compaction et la place des
  projections vers le backend et vers les clients : revue ci-dessus.
- [ ] Implémenter la préparation du contexte et la compaction réunies dans Context,
  conserver Session canonique et les projections clientes distinctes.
- [ ] Réexaminer AgentSpec / AgentTrigger / WorkerProfile et la construction des
  spécialistes à partir de la déclaration et du profil résolu.
- [x] Le plugin mémoire possède désormais le décodage de son XML depuis les
  slots de contexte retenus ; Realtime ne connaît plus les candidats mémoire.
- [x] Retirer Portrait et MediaReference du cœur : payloads, exports et exceptions
  de projection supprimés, références distantes du Harness nettoyées. Tests de
  préfixe de contexte conservés avec des messages/images génériques et des limites
  vision communes. Ancienne politique de notation et d'identité incarnée retirée,
  documentation et fixture neutralisées ; primitives Image/Artifact conservées.
  Validation : 653 tests Framework réussis (3 skips), 155 Harness, 53 tests JS
  et build Harness réussis. Aucun alias ou migration ajouté.
- [x] `critical=False` couvre les erreurs des factories de services et du start.
  Déclarations invalides et dépendants critiques restent fatals ; santé explicite
  et aucune contribution serveur exposée pour une construction indisponible.

## Relecture avant refonte — prérequis ciblés, 18 octobre

- [x] **Références et RPC du navigateur.** Chaque capture invalide les anciens
  identifiants, y compris ceux des nœuds cachés/tronqués. Déconnexion/expiration du
  client RPC : annulation de l'opération en cours. Tests Chromium et annulation
  couverts ; parcours Réglages/Retour et fermeture d'un aperçu authentifié vérifiés
  sur l'installation, sans perte du chat principal.
- [x] **Fermeture bloquée pendant un rechargement.** Reproduction Chromium du
  Harness 1.62 : `Target.closeTarget` répond success:true sans détruire la cible,
  puis Playwright reste en état closing et les retries ordinaires n'émettent plus
  de commande. Même signature que l'ancien aperçu, y compris après navigation vers
  about:blank. Bug amont : https://github.com/microsoft/playwright/issues/42068.
  Correction : fermeture normale bornée à 5 s, puis reprise du seul onglet via
  `close(run_before_unload=True)` avec attente de son événement close (5 s maximum).
  Aucune fermeture globale du contexte ni du chat. Erreur explicite si la reprise
  échoue et warning sans contenu/URL. Essai 30 cycles : 29 blocages récupérés,
  autre onglet et brouillon intacts. Tests généraux de récupération/échec/annulation
  et laboratoire agentique de course reload/close séparés. L'ancien onglet disparu
  ne permet pas de prouver rétrospectivement son déclencheur exact.
  Validation : 21 tests navigateur ciblés ; suite complète 691 succès/3 skips,
  Harness 157 succès. Essai Chromium visible : 10 blocages/10 récupérations ;
  délais de production : récupération d'un onglet déjà bloqué en 5,03 s.

- [x] **Frontières des plugins.** XML mémoire déplacé dans MemoryLiveContext, à
  partir des provider_slots retenus après ajustement au budget. Migration runtime
  des références historiques supprimée d'AgentSpec.from_profile ; profil résolu
  à réexaminer avec l'API des agents. Aucun changement des modèles de payloads.
- [x] **Dégradation d'un service optionnel.** Échec de construction visible dans
  la santé, contributions indisponibles retirées, dépendants respectant critical.
  Contrats invalides toujours fatals. Une nouvelle composition réessaie la factory.
  Conversation et sessions supplémentaires fonctionnelles sans service optionnel.

Validation des trois lots : 687 tests Framework réussis, 3 skips ; 157 tests
Harness réussis ; Ruff critique et diff vérifiés. Serveur installé redémarré avec
fenêtre conservée, puis daemon navigateur actualisé pour charger ses corrections.
Historique, archive, configuration et bindings de session conservés ; commandes
recalculées au démarrage. Aperçu neuf fermé et retour au chat contrôlés en réel.

Précision de Baptiste : la correspondance entre nom de classe et `type` des modèles
de payloads est une discipline volontaire, à conserver. Ces modèles sont hors du
périmètre du refactor ; le point de nettoyage proposé sur leur décodage est retiré.
La refonte vise les objets runtime et leur assemblage.

Les propriétaires ApplicationSpec/ApplicationContext/ApplicationRuntime, les scopes
application/session, les formes Plugin/AgentExtension/ServerExtension et le trajet
AgentSpec/WorkerProfile relèvent de la refonte elle-même. Ne pas les restructurer
une première fois au seul titre du nettoyage. Sécurité locale/distante et nouveau
client mobile restent des sujets dédiés, sans extraction automatique supplémentaire.

Audit : lecture du Framework/Harness, trois sondes ponctuelles, aucun code runtime
modifié et aucune intervention sur les processus utilisateur.

## Livré dans cette passe

- [x] **Sessions indépendantes — 18 octobre.** ApplicationContext héberge des
  workers concurrents avec API/sockets par session, reprise paresseuse des sessions
  persistées, sessions résidentes et arrêt explicite. Session devient la source
  canonique de l'historique, configuration, état des extensions et bindings ;
  suppression des callbacks et du magasin de bindings concurrents. AgentResources
  fournit client et valeurs initiales. Verrou d'écriture worker, écritures atomiques,
  nettoyage d'un démarrage annulé et route conservée lors d'un reset.
  Services de portée application partagés, services de portée session isolés
  (dont texte/voix) ; bucket/navigateur/surfaces gardent leur portée application.
  Les flottes de spécialistes utilisent les mêmes AgentSpec/sessions et routent
  résultats et activité vers leur seul appelant ; retour visuel transitoire
  « sous-agent » dans le starter/Harness. Le Harness conserve sa conversation
  par défaut ; aucun sélecteur multi-conversation ajouté à son interface.
  Les ponts desktop/distants applicatifs restent attachés à cette conversation ;
  leur sélection de session sera une évolution produit explicite.
  Validation : 659 tests Framework, 3 skips ; 156 tests Harness ; 26/54 tests JS
  starter/Harness, builds et contrôle visuel des deux états de sous-agent réussis.
  Reprise navigateur/starter/aperçus, isolation voix/config/bindings et absence de
  worker orphelin après annulation vérifiées. Installation basculée par script
  ponctuel retiré après exécution, historique/réglages/fenêtre conservés.


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
