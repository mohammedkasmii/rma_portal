"""Catalog of every OmegaFlow workflow captured on 23 September 2026.

Each :class:`~rma_portal.domain.workflow_definition.WorkflowDefinition` records
only what the four ScriptScrap captures actually show: the route, the Knack view
id, the procedure filter values (exact captured option values), the rendered
column classes and the shared-detail date fields. Nothing here is a guess:

* a column is listed only when its ``field_N`` class appeared in the captured
  table header of that view;
* "unclassified" action buttons that have no ``field_N`` class in the capture
  (for example "Envoi 2ème expert" or "Validation FFT") are deliberately not
  modelled, since they cannot be selected reliably;
* empty captured queues (Appréciation, Hifad-filtered agreement, EXPC 2ème
  expert, Réserves en cours) are registered normally: their DOM and response
  contract is known, they simply produced zero rows;
* the Expertise collégiale stages declare ``follows`` (the path documented in
  docs/multi-workflow-system-design.md); no other pipeline is assumed;
* every workflow except ``agreement_garage`` is ``CAPTURE_DERIVED``: it runs
  with the conservative queue-membership policy and is shown as "À valider
  sur site" until the agency confirms it.

Evidence notes name the capture by the suffix of its directory
(``cbdfdf``, ``fc4958``, ``688e53``, ``00f0cc``); no customer data appears here.
"""

from __future__ import annotations

from collections.abc import Iterable

from rma_portal.domain.enums import NotificationClass, WorkflowRulesStatus
from rma_portal.domain.workflow_definition import (
    COMMON_FIELD_KEYS,
    FieldKind,
    FieldSource,
    FieldSpec,
    FilterSpec,
    WorkflowDefinition,
)

CATALOG_VERSION = 1
__all__ = ["CATALOG", "COMMON_FIELD_KEYS", "SHARED_DETAIL_FIELDS", "StaticWorkflowCatalog", "default_catalog"]

_AGREEMENT_CONTROL = "#kn-conn-1-field_219"

_ACTION = NotificationClass.ACTION
_INFO = NotificationClass.INFORMATIONAL
_DERIVED = WorkflowRulesStatus.CAPTURE_DERIVED


def _list(
    key: str,
    css: str,
    label: str,
    kind: FieldKind = FieldKind.CONTEXT,
    *,
    date: bool = False,
) -> FieldSpec:
    return FieldSpec(key, css, label, FieldSource.LIST, kind, date)


def _detail(key: str, css: str, label: str) -> FieldSpec:
    return FieldSpec(key, css, label, FieldSource.DETAIL, FieldKind.DATE, True)


# --- common identity columns --------------------------------------------------------------------
NUMBER = _list("dossier_number", "field_1", "Dossier", FieldKind.IDENTITY)
NAME = _list("insured_name", "field_3", "Nom", FieldKind.IDENTITY)
PROCEDURE = _list("procedure", "field_219", "Procédure", FieldKind.IDENTITY)
REGISTRATION = _list("registration", "field_8", "Immatriculation", FieldKind.IDENTITY)
GARAGE = _list("garage", "field_40", "Garage", FieldKind.IDENTITY)
STATUS = _list("portal_status", "field_77", "Statut", FieldKind.STATUS)
CITY = _list("city", "field_655", "Ville", FieldKind.IDENTITY)
OBSERVATIONS = _list("observation_count", "field_318", "Observations")
ACCIDENT_DATE = _list("accident_date", "field_15", "Date sinistre", FieldKind.IDENTITY, date=True)

# --- shared dossier detail (scene_20/view_23 and the collegial sub-views) ------------------------
D_CREATION = _detail("date_creation", "field_107", "Date création")
D_FIN_PREVUE = _detail("date_premiere_fin_prevue", "field_113", "Première date de fin prévue")
D_FIN_TRAVAUX = _detail("date_fin_travaux_prevue", "field_138", "Date fin de travaux prévue")
D_DEVIS_GARAGE = _detail("date_envoi_devis_garage", "field_114", "Date envoi devis garage")
D_PHOTOS_AVANT = _detail("date_photos_avant", "field_260", "Date photos avant")
D_ACCORD_EXPERT = _detail("date_accord_expert", "field_115", "Date accord expert")
D_ACCORD_COMPAGNIE = _detail("date_accord_compagnie", "field_452", "Date accord compagnie")
D_REJET_COMPAGNIE = _detail("date_rejet_accord_compagnie", "field_453", "Date rejet accord compagnie")
D_2E_ACCORD = _detail("date_2e_accord_expert", "field_158", "Date 2ème accord expert")
D_ACCORD_2E_EXPERT = _detail("date_accord_2eme_expert", "field_867", "Date accord 2ème expert")
D_PHOTO_EN_COURS = _detail("date_photo_en_cours", "field_278", "Date photo en cours")
D_PHOTOS_APRES = _detail("date_photos_apres_reparation", "field_132", "Date photos après réparation")
D_ENVOI_FACTURE = _detail("date_envoi_facture", "field_133", "Date envoi facture")
D_DEMANDE_2E_ACCORD = _detail("date_envoi_demande_2e_accord", "field_157", "Date envoi demande 2e accord")
D_ENVOI_ACCORD_EXPERT = _detail("date_envoi_accord_expert", "field_598", "Date envoi accord expert")
D_AVIS_DOMMAGE = _detail("date_avis_dommage", "field_852", "Date avis de dommage")
D_TRAITEMENT_COMPAGNIE = _detail("date_traitement_compagnie", "field_853", "Date traitement compagnie")

# Every detail field the reader extracts from a dossier detail page (present ones only).
SHARED_DETAIL_FIELDS: tuple[FieldSpec, ...] = (
    D_CREATION,
    D_FIN_PREVUE,
    D_FIN_TRAVAUX,
    D_DEVIS_GARAGE,
    D_PHOTOS_AVANT,
    D_ACCORD_EXPERT,
    D_ACCORD_COMPAGNIE,
    D_REJET_COMPAGNIE,
    D_2E_ACCORD,
    D_ACCORD_2E_EXPERT,
    D_PHOTO_EN_COURS,
    D_PHOTOS_APRES,
    D_ENVOI_FACTURE,
    D_DEMANDE_2E_ACCORD,
    D_ENVOI_ACCORD_EXPERT,
    D_AVIS_DOMMAGE,
    D_TRAITEMENT_COMPAGNIE,
)

# --- the agreement queue (scene_1059/view_1874), shared by five procedure variants ---------------
_AGREEMENT_FIELDS = (
    NUMBER,
    NAME,
    PROCEDURE,
    REGISTRATION,
    GARAGE,
    STATUS,
    CITY,
    OBSERVATIONS,
    _list("estimate_amount_raw", "field_100", "Devis garage (TTC)"),
    _list("photos_before_repair", "field_261", "Photos avant réparation", FieldKind.ACTION),
    _list("quote_entry", "field_104", "Devis", FieldKind.ACTION),
    _list("agreement_joined", "field_118", "Accord joint", FieldKind.ACTION),
    _list("agreement", "field_119", "Accord", FieldKind.ACTION),
    _list("column_149", "field_149", "Colonne 149 (non classée)", FieldKind.ACTION),
    _list("agreement_login", "field_300", "Login accord"),
)


def _agreement(
    key: str,
    name: str,
    option_label: str,
    option_value: str,
    order: int,
    *,
    garage: bool = False,
    rules: WorkflowRulesStatus = _DERIVED,
    evidence: str,
) -> WorkflowDefinition:
    # Garage agréé's quote-sent date is the employees' known reference; the other
    # procedures fall back to the creation date, then to the detection time.
    primary = ("date_envoi_devis_garage",) if garage else ("date_envoi_devis_garage", "date_creation")
    return WorkflowDefinition(
        key=key,
        name=name,
        category="Instance d'accord",
        route="#dossiers-en-instance-accord/",
        view_id="view_1874",
        scene_id="scene_1059",
        notification_class=_ACTION,
        rules_status=rules,
        sort_order=order,
        fields=(*_AGREEMENT_FIELDS, D_DEVIS_GARAGE, D_CREATION),
        primary_date=primary,
        detail_required=("date_envoi_devis_garage",),
        # Garage agréé keeps its historical behaviour: a blank quote date is re-read every poll.
        detail_refresh_seconds=0 if garage else 3600,
        filter=FilterSpec("field_219", "is", option_value, option_label, _AGREEMENT_CONTROL),
        evidence=evidence,
    )


CATALOG: tuple[WorkflowDefinition, ...] = (
    _agreement(
        "agreement_garage",
        "Instance d'accord - Garage agréé",
        "Garage agréé",
        "5ed644a2faf17c0015d8c367",
        10,
        garage=True,
        rules=WorkflowRulesStatus.CONFIRMED,
        evidence="Production since V1; page 1 of 2 in cbdfdf.",
    ),
    _agreement(
        "agreement_normal",
        "Instance d'accord - Procédure normale",
        "Procédure normale",
        "5eebcdf9c791c6001505bd70",
        20,
        evidence="fc4958: all 5 pages.",
    ),
    _agreement(
        "agreement_appreciation",
        "Instance d'accord - Appréciation",
        "Appréciation",
        "6039196ec1fce4001c27ab66",
        30,
        evidence="fc4958: valid empty result.",
    ),
    _agreement(
        "agreement_collegial_cid",
        "Instance d'accord - Expertise collégiale CID",
        "Expertise Collégiale CID",
        "5f3e5a57696b8700158842e6",
        40,
        evidence="fc4958: one page, two rows.",
    ),
    _agreement(
        "agreement_hifad",
        "Instance d'accord - Hifad",
        "Hifad",
        "5ee405e42e70690015f4dcb0",
        50,
        evidence="00f0cc: valid empty result (selected twice).",
    ),
    WorkflowDefinition(
        key="hifad_search",
        name="Dossiers Hifad",
        category="Hifad",
        route="#recherche-dossiers-hifad/",
        view_id="view_1867",
        scene_id="scene_1066",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=60,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            STATUS,
            OBSERVATIONS,
            ACCIDENT_DATE,
            # field_107 is rendered as "Date mis." in this list.
            _list("date_mission", "field_107", "Date mis.", FieldKind.DATE, date=True),
            _list("code_action", "field_529", "Code", FieldKind.ACTION),
            _list("photos_before_repair", "field_261", "Photos avant réparation", FieldKind.ACTION),
            _list("offer_report", "field_23", "Offre / rapport", FieldKind.ACTION),
            _list("report_action", "field_768", "Rapport", FieldKind.ACTION),
        ),
        primary_date=("date_mission",),
        evidence="688e53: one page, ten rows; one detail visit.",
    ),
    WorkflowDefinition(
        key="estimate_pending",
        name="Dossiers en instance de devis",
        category="Devis et photos",
        route="#dossiers-en-instance-devis-photos/",
        view_id="view_655",
        scene_id="scene_417",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=70,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            OBSERVATIONS,
            ACCIDENT_DATE,
            _list("date_mission", "field_107", "Date mission", FieldKind.DATE, date=True),
            _list("management_status", "field_269", "Statut gestion", FieldKind.STATUS),
            _list("photos_before_repair", "field_261", "Photos avant réparation", FieldKind.ACTION),
            _list("quote_entry", "field_100", "Saisie devis", FieldKind.ACTION),
        ),
        primary_date=("date_mission",),
        evidence="fc4958: both pages, 16 rows; the captured view carries a current-week filter.",
    ),
    WorkflowDefinition(
        key="photos_pending",
        name="Dossiers en instance Photos",
        category="Devis et photos",
        route="#dossiers-en-instance-photos/",
        view_id="view_787",
        scene_id="scene_499",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=80,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            _list("photos_instance", "field_653", "Instance photos", FieldKind.STATUS),
            _list("code_before", "field_529", "Code avant", FieldKind.STATUS),
            _list("code_in_progress", "field_558", "Code en cours", FieldKind.STATUS),
            _list("code_after", "field_531", "Code après", FieldKind.STATUS),
            _list("mission_email", "field_560", "Email mission"),
            _list("date_envoi", "field_652", "Date envoi", FieldKind.DATE, date=True),
            D_PHOTOS_AVANT,
            D_PHOTO_EN_COURS,
        ),
        primary_date=("date_envoi", "date_photos_avant", "date_photo_en_cours"),
        evidence="688e53: all 5 pages, 50 rows, four detail visits.",
    ),
    WorkflowDefinition(
        key="fft_garage_pending",
        name="Dossiers en instance de FFT - Garage agréé",
        category="Facturation FFT",
        route="#dossiers-en-instance-fft2/",
        view_id="view_90",
        scene_id="scene_78",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=90,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            CITY,
            OBSERVATIONS,
            _list("photos_after_repair", "field_124", "Photos après réparation", FieldKind.ACTION),
            _list("invoice", "field_125", "Facture", FieldKind.ACTION),
            _list("parts_invoice", "field_136", "Facture pièces", FieldKind.ACTION),
            _list("report_entry", "field_297", "Saisie rapport", FieldKind.ACTION),
            _list("fft_data", "field_23", "FFT", FieldKind.ACTION),
            _list("report_action", "field_768", "Rapport", FieldKind.ACTION),
            _list("report_action_2", "field_784", "Rapport (2)", FieldKind.ACTION),
            D_ENVOI_FACTURE,
            D_PHOTOS_APRES,
        ),
        primary_date=("date_envoi_facture", "date_photos_apres_reparation"),
        detail_required=("date_envoi_facture",),
        evidence="cbdfdf/688e53: one page, five rows; one detail visit.",
    ),
    WorkflowDefinition(
        key="second_agreement_pending",
        name="Dossiers en instance 2ème accord",
        category="2ème accord",
        route="#dossiers-en-instance-2-accord-pec2/",
        view_id="view_149",
        scene_id="scene_116",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=100,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            OBSERVATIONS,
            _list("estimate_amount_raw", "field_100", "Devis garage (TTC)"),
            _list("first_agreement", "field_160", "1er accord", FieldKind.ACTION),
            _list("complementary_documents", "field_156", "Documents complémentaires", FieldKind.ACTION),
            _list("quote", "field_104", "Devis", FieldKind.ACTION),
            _list("agreement_entry", "field_243", "Saisie accord", FieldKind.ACTION),
            _list("agreement_joined", "field_118", "Accord joint", FieldKind.ACTION),
            D_DEMANDE_2E_ACCORD,
            D_2E_ACCORD,
        ),
        primary_date=("date_envoi_demande_2e_accord", "date_2e_accord_expert"),
        detail_required=("date_envoi_demande_2e_accord",),
        evidence="688e53: one page, two rows; route taken from the captured navigation.",
    ),
    WorkflowDefinition(
        key="report_pending",
        name="Dossiers en instance rapport",
        category="Rapports",
        route="#dossiers-en-instance-rapport/",
        view_id="view_1857",
        scene_id="scene_1062",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=110,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            CITY,
            OBSERVATIONS,
            _list("report_entry", "field_297", "Saisie rapport", FieldKind.ACTION),
            _list("report_preview", "field_768", "Aperçu / rapport", FieldKind.ACTION),
        ),
        evidence="688e53: two procedure-filter results, both one page. Splitting by procedure is a "
        "catalog change, not a reader change.",
    ),
    WorkflowDefinition(
        key="collegial_first_expert",
        name="Expertise collégiale - 1er expert",
        category="Expertise collégiale",
        route="#dossiers-expertise-collegiale/",
        view_id="view_1186",
        scene_id="scene_734",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=120,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            _list("collegial_type", "field_850", "Type dossier collégial"),
            _list("collegial_status", "field_851", "Statut collégial", FieldKind.STATUS),
            _list("model_report", "field_854", "Modèle / rapport", FieldKind.ACTION),
            _list("collegial_sent", "field_515", "Dossier collégial envoyé", FieldKind.ACTION),
            _list("preliminary_agreement", "field_518", "Accord préliminaire", FieldKind.ACTION),
            _list("preliminary_report", "field_862", "Rapport préliminaire", FieldKind.ACTION),
            _list("arbitration", "field_905", "Arbitrage", FieldKind.ACTION),
            D_AVIS_DOMMAGE,
            D_TRAITEMENT_COMPAGNIE,
        ),
        primary_date=("date_avis_dommage", "date_traitement_compagnie"),
        detail_required=("date_avis_dommage",),
        evidence="fc4958: all 3 pages, 29 rows, three detail visits.",
    ),
    WorkflowDefinition(
        key="collegial_first_agreed",
        name="EXPC 1er expert avec accord",
        category="Expertise collégiale",
        route="#dossiers-expcoll-exp1-accord/",
        view_id="view_2051",
        scene_id="scene_1168",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=130,
        follows=("collegial_first_expert",),
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            _list("collegial_type", "field_850", "Type dossier collégial"),
            _list("collegial_status", "field_851", "Statut collégial", FieldKind.STATUS),
            _list("agreement", "field_119", "Accord", FieldKind.ACTION),
            _list("collegial_agreement_report", "field_862", "Accord / rapport collégial", FieldKind.ACTION),
            _list("report_preview", "field_768", "Aperçu rapport", FieldKind.ACTION),
            _list("contradictory_report", "field_868", "Rapport contradictoire", FieldKind.ACTION),
            _list("countersigned_report", "field_128", "Rapport contresigné", FieldKind.ACTION),
            _list("workflow_column", "field_621", "Colonne workflow", FieldKind.ACTION),
        ),
        evidence="fc4958: all 10 pages, 95 rows, three detail visits. The 'Envoi 2ème expert' action "
        "column has no captured field class and is not modelled.",
    ),
    WorkflowDefinition(
        key="collegial_second_agreed",
        name="EXPC 2ème expert - avec accord",
        category="Expertise collégiale",
        route="#dossiers-expcol-exp2-avec-accord",
        view_id="view_2054",
        scene_id="scene_1171",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=140,
        follows=("collegial_first_agreed",),
        fields=(
            NUMBER,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            # The captured header labels field_555 "proc." in this view.
            _list("procedure", "field_555", "Procédure", FieldKind.IDENTITY),
            _list("expert_1", "field_865", "Expert 1"),
            _list("collegial_status", "field_851", "Statut collégial", FieldKind.STATUS),
            _list("preliminary_report", "field_862", "Rapport préliminaire", FieldKind.ACTION),
            _list("date_envoi", "field_869", "Date envoi", FieldKind.DATE, date=True),
            _list("report", "field_128", "Rapport", FieldKind.ACTION),
        ),
        primary_date=("date_envoi",),
        evidence="688e53: valid empty result with rendered columns; non-empty layout still to validate live.",
    ),
    WorkflowDefinition(
        key="collegial_second_completed",
        name="EXPC 2ème expert - complétés",
        category="Expertise collégiale",
        route="#dossiers-expc-exp2-complts",
        view_id="view_2059",
        scene_id="scene_1175",
        notification_class=_INFO,
        rules_status=_DERIVED,
        sort_order=150,
        follows=("collegial_second_agreed",),
        fields=(
            NUMBER,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            _list("expert", "field_91", "Expert"),
            _list("expert_1", "field_865", "Expert 1"),
            _list("collegial_status", "field_851", "Statut collégial", FieldKind.STATUS),
            _list("report", "field_128", "Rapport", FieldKind.ACTION),
        ),
        evidence="688e53: valid empty result with rendered columns; non-empty layout still to validate live.",
    ),
    WorkflowDefinition(
        key="agreement_validated",
        name="Dossiers avec accord / validés",
        category="Accords et validations",
        route="#dossiers-avec-accord2",
        view_id="view_82",
        scene_id="scene_70",
        notification_class=_INFO,
        rules_status=_DERIVED,
        sort_order=160,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            OBSERVATIONS,
            _list("agreement", "field_119", "Accord", FieldKind.ACTION),
            _list("damages_ttc", "field_23", "Dommages TTC"),
        ),
        evidence="688e53: all 5 pages, 50 rows, one detail visit.",
    ),
    WorkflowDefinition(
        key="reformed",
        name="Dossiers réformés",
        category="Réformes",
        route="#dossiers-rforme",
        view_id="view_829",
        scene_id="scene_517",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=170,
        fields=(
            NUMBER,
            NAME,
            REGISTRATION,
            ACCIDENT_DATE,
            _list("nature", "field_661", "Nature", FieldKind.STATUS),
            _list("reform_notice", "field_672", "Avis de réforme", FieldKind.ACTION),
            _list("letter", "field_779", "Lettre", FieldKind.ACTION),
            _list("signed_letter", "field_666", "Lettre signée", FieldKind.ACTION),
            _list("report", "field_621", "Rapport", FieldKind.ACTION),
        ),
        evidence="688e53: all 5 pages, 50 rows, one detail visit. The captured header has no "
        "procedure, garage or status column.",
    ),
    WorkflowDefinition(
        key="deficiency_cancel_appreciation",
        name="Carence - annulation / appréciation",
        category="Carence",
        route="#dossiers-carence-/",
        view_id="view_1378",
        scene_id="scene_818",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=180,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            _list("guarantee", "field_16", "Garantie"),
            _list("date_mission", "field_756", "Date mission", FieldKind.DATE, date=True),
            _list("date_photos", "field_260", "Date photos", FieldKind.DATE, date=True),
            _list("date_agreement", "field_115", "Date accord", FieldKind.DATE, date=True),
            _list("date_intermediate_email", "field_918", "Date email intermédiaire", FieldKind.DATE, date=True),
        ),
        primary_date=("date_mission", "date_photos", "date_agreement"),
        evidence="688e53: all 3 pages, 25 rows. Titled 'Etape 2 : Modifier le statut du dossier'.",
    ),
    WorkflowDefinition(
        key="deficiency_intermediary",
        name="Carence - information intermédiaire",
        category="Carence",
        route="#dossiers-carence-/",
        view_id="view_1380",
        scene_id="scene_818",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=190,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            _list("guarantee", "field_16", "Garantie"),
            _list("date_mission", "field_756", "Date mission", FieldKind.DATE, date=True),
            _list("date_photos", "field_260", "Date photos", FieldKind.DATE, date=True),
            _list("date_agreement", "field_115", "Date accord", FieldKind.DATE, date=True),
            # Captured only in the sibling view_1378 header; kept first as the plan states and
            # resolved to the next captured date while this view does not render it.
            _list("date_intermediate_email", "field_918", "Date email intermédiaire", FieldKind.DATE, date=True),
        ),
        primary_date=("date_intermediate_email", "date_mission", "date_photos", "date_agreement"),
        evidence="688e53: all 5 pages, 50 rows, one shared detail visit. Titled 'Etape 1 : "
        "Information intermédiaire'.",
    ),
    WorkflowDefinition(
        key="reserves_pending",
        name="Réserves - en cours",
        category="Réserves",
        route="#dossiers-avec-rserve",
        view_id="view_1396",
        scene_id="scene_829",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=200,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            _list("doubt_status", "field_765", "Statut doute", FieldKind.STATUS),
            _list("summary", "field_776", "Synthèse", FieldKind.ACTION),
            _list("doubt_report", "field_772", "Rapport doute", FieldKind.ACTION),
        ),
        evidence="688e53: valid empty result with rendered columns.",
    ),
    WorkflowDefinition(
        key="reserves_company_instruction",
        name="Réserves - instruction compagnie",
        category="Réserves",
        route="#dossiers-avec-rserve",
        view_id="view_1421",
        scene_id="scene_829",
        notification_class=_ACTION,
        rules_status=_DERIVED,
        sort_order=210,
        fields=(
            NUMBER,
            NAME,
            PROCEDURE,
            REGISTRATION,
            GARAGE,
            STATUS,
            ACCIDENT_DATE,
            _list("doubt_report", "field_772", "Rapport doute", FieldKind.ACTION),
        ),
        evidence="688e53: both pages, 13 rows, one detail visit. No 'statut doute' column is "
        "rendered in this view.",
    ),
)


class StaticWorkflowCatalog:
    """The application-facing catalog (see ``application.ports.WorkflowCatalog``)."""

    def __init__(self, definitions: Iterable[WorkflowDefinition] = CATALOG) -> None:
        self._definitions = tuple(definitions)
        keys = [definition.key for definition in self._definitions]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate workflow keys in the catalog")
        self._by_key = {definition.key: definition for definition in self._definitions}

    def definitions(self) -> tuple[WorkflowDefinition, ...]:
        return self._definitions

    def get(self, key: str) -> WorkflowDefinition | None:
        return self._by_key.get(key)

    def shared_detail_fields(self) -> tuple[FieldSpec, ...]:
        return SHARED_DETAIL_FIELDS


def default_catalog() -> StaticWorkflowCatalog:
    return StaticWorkflowCatalog()
