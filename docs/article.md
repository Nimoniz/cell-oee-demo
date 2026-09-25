# De l'automate au navigateur : un TRS en direct, et surtout un TRS juste

![Une panne frappe la cellule : l'état passe au rouge, le TRS baisse en direct, puis la production reprend](demo.gif)

Dans beaucoup d'ateliers, le TRS se calcule après coup : un tableau Excel alimenté par des relevés papier, une semaine de retard, et la moitié des arrêts rangée sous « autre ». J'ai voulu montrer l'alternative de bout en bout, de l'automate au navigateur : un TRS calculé en direct à partir des signaux de la machine, et un opérateur qui qualifie ses arrêts sur une tablette pendant que la cause est encore fraîche.

Le résultat est un projet de démonstration, pas un produit : une cellule simulée, une pile de cinq services, et une seule commande pour tout lancer. Voici ce que j'y ai appris, surtout sur le calcul du TRS, où j'ai fini par comprendre quelque chose que je n'avais pas vu au départ.

## La chaîne, de bout en bout

![Architecture : simulator, collector, db, api, frontend](architecture.svg)

La cellule est une cellule robotisée de caisse en blanc, avec trois postes en série : OP10 (assemblage avant soudure), OP20 (soudage par points robotisé, le poste goulot qui fixe le temps de cycle) et OP30 (assemblage et contrôle après soudure). Un simulateur la joue et l'expose comme le ferait le serveur OPC UA d'un S7-1500 : mêmes types de données, mêmes noms de variables organisés comme un bloc de données.

Un collecteur s'abonne à ces variables (des souscriptions, pas de la scrutation), détecte les changements d'état et les arrêts, et écrit tout dans PostgreSQL avec TimescaleDB. Une API FastAPI calcule le TRS à la lecture, sans rien pré-agréger, et alimente trois écrans Next.js : la vue live, la qualification des arrêts pour l'opérateur, et l'analyse (Pareto, TRS par poste et par jour, courbe d'usure des électrodes).

Le TRS est calculé à l'échelle de la cellule seulement. Les postes ne servent qu'à localiser l'origine d'un arrêt.

## Le TRS en temps : ce que j'ai compris en cours de route

J'avais écrit un test de référence très tôt : le TRS doit toujours être égal au temps utile divisé par le temps requis, pour toute période et tout regroupement. Si ce test échoue, il y a un bug. Il passait, y compris avec des générateurs de données aléatoires (arrêts, trous de communication, compteurs qui débordent ou repartent à zéro).

C'est à la revue du jour 3 que je me suis arrêté sur la qualité. La formule que tout le monde écrit, c'est pièces bonnes ÷ pièces produites. Mais ce n'est pas la définition de la norme française **NF E60-182**, qui raisonne en temps. Chaque pièce vaut son temps de cycle théorique (TCT), et on définit :

- **temps requis** = temps d'ouverture − arrêts planifiés ;
- **temps de fonctionnement** = temps requis − (pannes + réglages + manques de pièces + engorgements + manuel) ;
- **temps net** = pièces produites × TCT ;
- **temps utile** = pièces bonnes × TCT.

Puis :

- Disponibilité = temps de fonctionnement ÷ temps requis
- Performance = temps net ÷ temps de fonctionnement
- Qualité = temps utile ÷ temps net

Multipliez les trois : tout se simplifie en chaîne, et il reste **temps utile ÷ temps requis**. C'est ce que mon test vérifiait, mais je voyais désormais pourquoi il est vrai : ce n'est pas une coïncidence numérique, c'est la construction même de la norme. Et pièces bonnes ÷ pièces produites n'en est que le cas particulier où le TCT ne change jamais, ce qui est le cas de ma démo.

La conséquence pratique m'a convaincu : si le TCT change en cours de période, la définition en temps reste exacte, et le raccourci par comptage de pièces devient faux. C'est pourquoi le TCT est stocké avec chaque échantillon de compteur, et pourquoi la section TRS de mon `CLAUDE.md` a été réécrite en temps, norme citée, avec « bonnes ÷ total » indiqué comme cas particulier. Une définition, un invariant, et tout ce qui le casserait se voit immédiatement.

Deux règles d'hygiène vont avec. Une valeur indéfinie vaut `null`, jamais 0 : une période sans temps requis n'a pas un TRS de 0 %, elle n'en a pas. Et un arrêt de moins de 120 secondes est un micro-arrêt : il ne compte pas comme un arrêt pour la disponibilité, il n'est pas proposé à l'opérateur, et son temps reste dans le temps de fonctionnement, donc il apparaît comme une perte de performance. C'est le cas du rodage des électrodes (30 secondes tous les 150 pièces environ). On juge un arrêt sur sa durée totale, jamais sur la part qui tombe dans la période regardée.

## Trois pièges qui ne se voient pas sur un schéma

**L'horodatage source, jamais l'heure d'arrivée.** Chaque valeur OPC UA porte un `SourceTimestamp`. Le collecteur et le calcul du TRS n'utilisent que lui. Ma simulation tourne sur sa propre horloge, accélérée jusqu'à 60 fois, si bien que l'heure d'arrivée n'a aucun sens : la seule heure qui compte est celle que l'automate a posée.

**Un trou de communication n'est pas une panne.** Le collecteur surveille un compteur de vie (heartbeat) que l'automate incrémente chaque seconde. S'il ne bouge plus, la communication est perdue : je consigne un trou et je l'exclus du temps requis, au lieu de compter un arrêt qui n'a peut-être pas eu lieu. Le délai est plus subtil qu'il n'y paraît : cinq secondes simulées valent 83 millisecondes réelles à ×60, sous le bruit normal du réseau, donc le seuil est le maximum de 5 secondes simulées et de 3 secondes réelles.

**Les compteurs cumulés se comportent mal.** Une diminution veut dire un redémarrage de l'automate ou un débordement, et il ne faut jamais en tirer une production négative. Les écritures sont idempotentes, avec des clés naturelles sur l'horodatage source : redémarrer le collecteur ne crée ni doublon ni arrêt fantôme.

## Ce que le premier `docker compose up` m'a appris

L'objectif était qu'une seule commande donne un écran vivant, avec de l'historique déjà en place. Une étape d'amorçage rejoue le simulateur à toute vitesse sur quelques jours simulés, l'écrit par le même chemin qu'un vrai collecteur, puis passe la main à la simulation en direct.

Sur une machine propre, cela a cassé de deux façons, alors que tous mes tests étaient verts :

- Mon amorçage vérifiait si la base contenait déjà de l'historique avant d'avoir créé les tables. Mes tests migraient toujours la base avant, donc ils ne pouvaient pas le voir. Seule une base vraiment vierge le révélait.
- Le serveur Next.js écoutait uniquement sur l'adresse réseau du conteneur, pas sur `127.0.0.1` : le healthcheck échouait en boucle alors que l'appli fonctionnait très bien depuis mon navigateur.

Deux leçons : un test qui ne part jamais d'un état vierge ne teste pas le premier démarrage, et « ça marche chez moi » ne vaut rien pour une démo dont la promesse est de marcher chez les autres. Un test de bout en bout, qui monte toute la pile depuis zéro et charge les trois écrans, fait désormais partie du dépôt.

Le premier démarrage laisse aussi exactement un trou de communication : le temps que les conteneurs démarrent, le collecteur ne regardait pas encore. Je le documente comme un comportement attendu, pas comme un bug, et un test vérifie qu'il reste borné.

## Et sur un vrai S7-1500 ?

L'espace d'adressage reproduit un bloc de données publié par le serveur OPC UA de la CPU. Les types se correspondent directement (`Int`, `UDInt`, `Real`, `Bool`), et le collecteur lit l'origine d'un arrêt dans une variable dédiée au lieu de la déduire, ce que le programme automate doit donc écrire en même temps que l'état. Les identifiants de nœuds diffèrent, et la correspondance est isolée à un seul endroit du code. Côté automate, il faut activer le serveur OPC UA de la CPU (avec la licence que cela demande) et publier les membres du bloc. Je renvoie à la documentation de votre firmware pour les étapes exactes.

Le point qui ne se transpose pas tel quel, c'est la sécurité. Mon simulateur accepte des connexions anonymes et non chiffrées, ce qui n'est acceptable que pour une cellule simulée sur votre propre machine. Sur un vrai automate : politique Sign & Encrypt, certificats approuvés des deux côtés, un utilisateur dédié en lecture seule, l'automate sur le réseau OT, et le port OPC UA joignable seulement par le collecteur à travers un pare-feu. Mon collecteur ne configure pas encore cette sécurité, et l'ajouter serait le premier travail d'un vrai déploiement.

## Ce que cette démo n'est pas

Ce n'est pas un produit. Il n'y a ni authentification, ni plusieurs cellules, ni lien avec un ERP ou un MES. La cellule est simulée : elle est crédible (usure des électrodes qui dégrade puis restaure la qualité, pannes par code défaut, pauses planifiées, manques de pièces et engorgements) mais ce n'est pas une vraie ligne. Elle sert à montrer la chaîne et à discuter du calcul.

J'ai construit ce projet avec l'aide de Claude Code, et le fichier `CLAUDE.md` du dépôt, qui fixe les définitions du TRS et les règles du collecteur, en garde la trace.

## Le code

Le dépôt contient tout, avec un README bilingue et les définitions complètes : https://github.com/Nimoniz/cell-oee-demo

Si vous travaillez sur un vrai atelier, je serais curieux de savoir ce que vous corrigeriez en premier dans cette chaîne.
