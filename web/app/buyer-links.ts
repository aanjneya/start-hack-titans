export const mailHref = (id: number) => `/?view=emails&mail=${id}`;
export const findingHref = (id: number) => `/?view=findings&finding=${id}`;
export const actionHref = (key: string) => `/?action=${encodeURIComponent(key)}`;
export const followupHref = (id: number) => `/?followup=${id}#followup-${id}`;
