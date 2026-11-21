// How our clean tables read in SAP terms (presentation only; the backend keeps its own field names).
// sap = SAP table-field, label = plain words. Fields without a standard SAP equivalent have sap "".

export type SapField = { key: string; sap: string; label: string };
export type SapTable = { table: string; sap: string; label: string; fields: SapField[] };

const f = (key: string, sap: string, label: string): SapField => ({ key, sap, label });

export const SAP_TABLES: SapTable[] = [
  {
    table: "purchase_orders", sap: "EKKO / EKPO", label: "Purchase order items",
    fields: [
      f("po_number", "EBELN", "PO"), f("po_line", "EBELP", "Item"), f("supplier_id", "LIFNR", "Vendor"),
      f("material_id", "MATNR", "Material"), f("plant", "WERKS", "Plant"), f("quantity", "MENGE", "Quantity"),
      f("unit_price", "NETPR", "Net price"), f("currency", "WAERS", "Currency"), f("order_date", "BEDAT", "PO date"),
      f("requested_date", "EINDT", "Delivery date"), f("promised_date", "EKES-EINDT", "Confirmed date"),
      f("actual_delivery_date", "MSEG-BUDAT", "Delivered on"), f("status", "PROCSTAT", "Status"),
      f("buyer", "ERNAM", "Created by"), f("incoterm", "INCO1", "Incoterms"), f("note", "", "Note"),
    ],
  },
  {
    table: "order_confirmations", sap: "EKES", label: "Vendor confirmations",
    fields: [
      f("confirmation_id", "XBLNR", "Confirmation"), f("po_number", "EBELN", "PO"), f("po_line", "EBELP", "Item"),
      f("supplier_id", "LIFNR", "Vendor"), f("material_id", "MATNR", "Material"), f("quantity", "MENGE", "Quantity"),
      f("ship_date", "", "Ship date"), f("delivery_date", "EINDT", "Delivery date"), f("revision", "REVLV", "Revision"),
      f("unit_price", "NETPR", "Net price"), f("currency", "WAERS", "Currency"), f("bank_iban", "IBAN", "Bank IBAN"),
      f("note", "", "Note"),
    ],
  },
  {
    table: "goods_receipts", sap: "MKPF / MSEG", label: "Goods receipts",
    fields: [
      f("gr_number", "MBLNR", "Material doc."), f("gr_date", "BUDAT", "Posting date"), f("po_number", "EBELN", "PO"),
      f("po_line", "EBELP", "Item"), f("supplier_id", "LIFNR", "Vendor"), f("material_id", "MATNR", "Material"),
      f("plant", "WERKS", "Plant"), f("qty_ordered", "EKPO-MENGE", "Ordered"), f("qty_received", "ERFMG", "Received"),
      f("delivery_note", "LFSNR", "Delivery note"), f("inspector", "", "Inspector"), f("remark", "SGTXT", "Text"),
    ],
  },
  {
    table: "stock", sap: "MARD / MCHB", label: "Stock by batch",
    fields: [
      f("snapshot_date", "", "As of"), f("site", "WERKS", "Plant"), f("material_id", "MATNR", "Material"),
      f("batch", "CHARG", "Batch"), f("quantity", "CLABS", "Unrestricted"), f("uom", "MEINS", "Unit"),
      f("safety_stock", "EISBE", "Safety stock"), f("weeks_of_cover", "", "Weeks of cover"),
      f("last_count", "", "Last count"), f("revision", "REVLV", "Revision"),
    ],
  },
  {
    table: "shipments", sap: "VTTK", label: "Inbound shipments",
    fields: [
      f("shipment_id", "TKNUM", "Shipment"), f("carrier_id", "TDLNR", "Carrier"), f("mode", "VSART", "Mode"),
      f("reference", "SIGNI", "Container / AWB"), f("supplier_id", "LIFNR", "Vendor"), f("route_id", "ROUTE", "Route"),
      f("origin", "", "From"), f("destination", "", "To"), f("via", "", "Via"), f("dest_site", "WERKS", "Plant"),
      f("etd", "DPABF", "ETD"), f("eta", "DPTEN", "ETA"), f("status", "STTRG", "Status"),
      f("last_lat", "", "Last lat"), f("last_lon", "", "Last lon"), f("note", "", "Note"),
    ],
  },
  {
    table: "materials", sap: "MARA / MAKT", label: "Materials",
    fields: [
      f("component_id", "MATNR", "Material"), f("description", "MAKTX", "Description"),
      f("category", "MATKL", "Material group"), f("uom", "MEINS", "Unit"), f("abc_class", "MAABC", "ABC"),
      f("critical", "", "Critical"), f("drawing_rev", "REVLV", "Revision"),
    ],
  },
  {
    table: "bom", sap: "STKO / STPO", label: "Bill of materials",
    fields: [
      f("parent_id", "MATNR", "Parent"), f("child_id", "IDNRK", "Component"), f("qty_per", "MENGE", "Qty per"),
      f("valid_from", "DATUV", "Valid from"), f("valid_to", "", "Valid to"),
    ],
  },
  {
    table: "products", sap: "MARA", label: "Finished goods",
    fields: [
      f("product_id", "MATNR", "Material"), f("name", "MAKTX", "Description"), f("product_line", "PRDHA", "Product line"),
      f("lifecycle", "MSTAE", "Status"), f("launch_date", "", "Launch date"), f("weekly_plan_qty", "", "Weekly plan"),
      f("day_one_qty", "", "Day-one demand"), f("std_cost_eur", "STPRS", "Standard price"), f("valid_from", "", "Valid from"),
    ],
  },
  {
    table: "suppliers", sap: "LFA1", label: "Vendors",
    fields: [
      f("supplier_id", "LIFNR", "Vendor"), f("vendor_no", "", "Vendor no. (old)"), f("name", "NAME1", "Name"),
      f("legal_name", "NAME2", "Legal name"), f("category", "", "Category"), f("country", "LAND1", "Country"),
      f("city", "ORT01", "City"), f("currency", "WAERS", "Currency"), f("payment_terms", "ZTERM", "Payment terms"),
      f("incoterm", "INCO1", "Incoterms"), f("status", "", "Status"), f("qualification_status", "", "Qualification"),
      f("qualification_valid_until", "", "Qualified until"), f("contact_name", "", "Contact"),
      f("contact_email", "SMTP_ADDR", "Email"), f("language", "SPRAS", "Language"), f("bank_iban", "IBAN", "Bank IBAN"),
      f("notes", "", "Notes"),
    ],
  },
  {
    table: "supplier_part_map", sap: "EINA / EINE", label: "Purchasing info records",
    fields: [
      f("supplier_id", "LIFNR", "Vendor"), f("component_id", "MATNR", "Material"), f("supplier_ref", "IDNLF", "Vendor part no."),
      f("share", "", "Share"), f("primary", "", "Main source"), f("lead_time_days", "APLFZ", "Lead time (days)"),
      f("moq", "MINBM", "Min. order qty"), f("unit_price", "NETPR", "Net price"), f("currency", "WAERS", "Currency"),
      f("note", "", "Note"),
    ],
  },
  {
    table: "engineering_changes", sap: "AENR", label: "Engineering changes",
    fields: [
      f("change_id", "AENNR", "Change no."), f("parent_id", "MATNR", "Parent"), f("child_id", "IDNRK", "Component"),
      f("action", "", "Action"), f("rev_old", "", "Old revision"), f("rev_new", "REVLV", "New revision"),
      f("use_up_old_rev", "", "Use up old"), f("effective_from", "DATUV", "Valid from"), f("reason", "AETXT", "Reason"),
    ],
  },
  {
    table: "packout", sap: "AFRU", label: "Packing confirmations",
    fields: [
      f("pack_date", "BUDAT", "Date"), f("site", "WERKS", "Plant"), f("product_id", "MATNR", "Material"),
      f("line", "ARBPL", "Line"), f("units_packed", "LMNGA", "Units packed"),
    ],
  },
  {
    table: "launch_status", sap: "", label: "Launch status",
    fields: [
      f("as_of", "", "As of"), f("product_id", "MATNR", "Material"), f("edition", "", "Edition"),
      f("day_one_demand", "", "Day-one demand"), f("packed", "", "Packed"), f("still_to_pack", "", "Still to pack"),
      f("rate_per_hour", "", "Rate per hour"), f("line_status", "", "Line status"),
    ],
  },
];

export function sapCell(value: unknown): string {
  if (value == null || value === "") return "";
  if (typeof value === "number") return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
