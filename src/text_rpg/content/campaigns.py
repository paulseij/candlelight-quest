"""Original, executable scenario families; all named sites are existing world IDs."""

CAMPAIGNS = {
    'iron_coast': {
        'title': 'The Broken Beacon', 'hub': 'saltmere_docks',
        'motives': [
            'The accounts suggest an attempt to profit from ships diverted toward private unloading crews.',
            'The accounts suggest someone wanted to discredit the harbor authority and win a lucrative signal contract.',
            'The accounts suggest a scheme to collect compensation for fabricated cargo losses.',
        ],
        'contacts': ['harbormaster_kael', 'captain_mora', 'barkeep_sal'],
        'opening': 'A posted harbor warning describes a ship nearly wrecked by a false signal. The harbor needs evidence before anyone is accused. Compare the signal records, repair orders, or cargo papers.',
        'chapters': [
            {'title': 'False lights',
             'sites': [
                 ['saltmere_watchtower', 'signal records', 'The duty ledger has a scraped-out entry. Beneath it, a second signal was ordered while the authorized lamp was unlit. The order carries a split-circle seal.'],
                 ['shipwright_yard', 'repair orders', 'A duplicate repair order requests a shutter that fits a signaling lantern, not a ship. Its split-circle seal matches a receiving slip filed with the harbor notices.'],
                 ['smuggler_den', 'cargo papers', 'A waxed packet hidden behind loose packing lists describes payment for a false light. The payer uses a split-circle seal; the crew were hired hands, not the planners.'],
             ],
             'expose': 'You publish copies of the corroborating evidence at the harbor notice board. The false-light method is now public; the sponsor must abandon it. A payment reference gives you a new lead.',
             'secure': 'You file the evidence and replace the compromised signaling instructions with a verified copy at the harbor office. The immediate danger is contained, but the sponsor is still concealed. A payment reference gives you a new lead.',
             'pressure': ['The warning board now lists delayed sailings while crews check every signal.', 'A second notice reports lost trading time. The false-light problem remains unresolved; the paper trail is still available.'],
            },
            {'title': 'Follow the money',
             'sites': [
                 ['saltmere_guildhall', 'payment register', 'Public freight accounts link the split-circle payments to a shell freight account. Two separate receipts identify the same account and its withdrawal instructions.'],
                 ['saltmere_market', 'freight receipts', 'A stallholder has pinned disputed freight receipts to the market board. The dates match the false-light payments; the supposed cargo was never delivered.'],
                 ['storm_point_lighthouse', 'dispatch copies', 'The dispatch archive contains rejected instructions for a second false signal. The freight account appears again, alongside a route used to collect its payments.'],
             ],
             'expose': 'You publish the matching payment records and collection route. The anonymous freight account can no longer conceal its involvement. Your dossier remains on file; this investigation is closed.',
             'secure': 'You register the verified payment trail and withdrawal warning at the harbor office. The compromised instructions are blocked and the evidence preserved without a public accusation. This investigation is closed.',
             'pressure': ['An amended freight notice uses another account name. The archived receipts still connect it to your investigation.', 'The collection route is being wound down. Copies already lodged in the public archives preserve the evidence.'],
            },
        ],
    },
    'ashen_highlands': {
        'title': 'The Ashen Ledger', 'hub': 'cinderhold_gate',
        'motives': [
            'The freight accounts suggest resale of the missing safety stores to an unregistered worksite.',
            'The freight accounts suggest someone engineered stoppages to profit from emergency hauling contracts.',
            'The freight accounts suggest a private contractor hid dangerous shortages before an inspection.',
        ],
        'contacts': ['mine_boss_gretta', 'forgemaster_durgan', 'tavern_keeper_hild'],
        'opening': 'A notice at Cinderhold warns that safety supplies are disappearing between the forge and the mines. Workers want the records checked before another shift goes underground. Three surviving paper trails offer a starting point.',
        'chapters': [
            {'title': 'Missing safety stores',
             'sites': [
                 ['cinderhold_forge', 'dispatch ledger', 'The dispatch ledger records reinforced supports leaving the forge. A duplicate requisition diverts them under a split-circle seal; the forge copy preserves the original destination.'],
                 ['miner_quarters', 'shift notices', 'Workers have pinned their rejected requests beside the shift roster. The same supports were billed to two crews, but neither delivery was signed for. Both requests carry a split-circle seal.'],
                 ['deep_mines', 'supply tally', 'An empty supply rack still carries its tally. Someone changed the destination after dispatch; the split-circle authorization appears beneath a layer of coal dust.'],
             ],
             'expose': 'You post the matching records at Cinderhold Gate. Workers can identify the falsified requisitions and refuse those unsafe assignments. A freight reference reveals where the stores were redirected.',
             'secure': 'You lodge the matching records and a hold on the suspect requisitions at Cinderhold Gate. The unsafe assignments are flagged while the accusation remains private. A freight reference reveals the diversion route.',
             'pressure': ['A shift notice records another delay while crews check their missing supports.', 'The gate board reports a work stoppage on the affected seam. The missing stores still need accounting for.'],
            },
            {'title': 'The diversion route',
             'sites': [
                 ['highland_road', 'freight register', 'A copy of the public freight register is posted at the road toll marker. It records support timber as waste stone. The diversion authorization links directly to the missing stores.'],
                 ['cinderhold_tavern', 'carrier receipts', 'Disputed carrier receipts have been left on the common notice board. Their weights match the missing supports, not the waste stone described on the manifests.'],
                 ['old_mine_shaft', 'discarded labels', 'Shipping labels caught beneath an abandoned cart show both destinations: the working seam was crossed out in favor of a private freight account.'],
             ],
             'expose': 'You publish the matched freight records. The diversion can no longer pass for routine waste hauling. The workers retain a permanent copy and the investigation closes.',
             'secure': 'You file the matched freight records and block the fraudulent requisitions at the gate office. The evidence remains available for later proceedings; the investigation closes.',
             'pressure': ['A revised freight notice changes the collection hour. The older records remain on file.', 'The suspect hauling contract is being withdrawn. Archived copies preserve the trail instead of erasing your progress.'],
            },
        ],
    },
}

PERSONAL_GOALS = {
    'knowledge': 'Preserve a complete account of what happened, including both independent evidence trails.',
    'community': 'Protect the community by resolving the immediate danger and its underlying supply or payment scheme.',
    'justice': 'Make the corroborated evidence public in both chapters.',
    'discretion': 'Resolve both chapters through documented safeguards without public accusations.',
}
