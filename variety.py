"""Variété des combinés : ne pas redonner les mêmes équipes.

Deux informations, gardées par jour par GenerationService, décrivent ce qui a déjà été distribué aujourd'hui :
  - `seen` : les matchs déjà donnés à CE client (dans une de ses générations du jour) ;
  - `served` : pour chaque match, le nombre d'AUTRES clients (la génération automatique compte comme un client) qui l'ont reçu.

Elles servent à deux endroits :
  - choose_pool : parmi les matchs du jour, ceux qu'on analyse pour cette génération. Ceux que le client n'a pas encore reçus
    passent avant, avec un match de chaque championnat avant le deuxième de chacun, puis les moins distribués, puis les plus
    proches du coup d'envoi ;
  - ComboGenerator.build_combos : un pronostic sur un match déjà reçu par le client est très fortement pénalisé, un match
    déjà reçu par d'autres clients, modérément (3 clients au plus comptés).
Un jour de peu de matchs, il n'y a pas d'autre choix que de redonner des matchs : les pénalités s'y plient (elles ne
suppriment aucun match), la génération réussit quand même.
"""
from dataclasses import dataclass, field
from typing import Mapping

import config


@dataclass(frozen=True)
class Variety:
    seen: frozenset = frozenset()
    served: Mapping = field(default_factory=dict)

    def is_seen(self, match_id):
        return str(match_id) in self.seen

    def times_served(self, match_id):
        """Combien d'autres clients ont déjà reçu ce match aujourd'hui."""
        return int(self.served.get(str(match_id), 0))


def choose_pool(matches, variety=None, limit=None):
    """Les matchs analysés pour cette génération, parmi tous les matchs à venir du jour (dans l'ordre des championnats, puis du
    coup d'envoi) : au plus `limit` (config.MATCHS_PAR_GENERATION).

    On prend d'abord les matchs que le client n'a pas encore reçus, puis, championnat après championnat, le premier de chacun
    avant le deuxième de chacun, et ainsi de suite : tous les championnats qui jouent sont représentés avant qu'un seul en ait
    trois, et un jour où un seul championnat joue, il remplit tout seul la liste. Dans un championnat, le premier est celui que
    le client n'a pas reçu, le moins distribué à d'autres clients, puis le plus proche du coup d'envoi. Le résultat garde
    l'ordre d'entrée."""
    variety = variety or Variety()
    limit = config.MATCHS_PAR_GENERATION if limit is None else limit

    by_league, order = {}, []
    for position, match in enumerate(matches):
        league = match.get("league")
        if league not in by_league:
            by_league[league] = []
            order.append(league)
        by_league[league].append((position, match))

    ranked = []                                   # (clé de tri global, position d'origine, match)
    for league_index, league in enumerate(order):
        chosen = sorted(by_league[league],
                        key=lambda item: (variety.is_seen(item[1]["id"]), variety.times_served(item[1]["id"]), item[0]))
        for rank, (position, match) in enumerate(chosen):
            key = (variety.is_seen(match["id"]), rank, variety.times_served(match["id"]), league_index, position)
            ranked.append((key, position, match))
    if len(ranked) > limit:
        ranked = sorted(ranked, key=lambda item: item[0])[:max(0, limit)]
    ranked.sort(key=lambda item: item[1])
    return [match for _key, _position, match in ranked]
