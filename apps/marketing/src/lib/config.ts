import { publicOrigins } from "../../../../packages/web-config/public-origins";

export { publicOrigin } from "../../../../packages/web-config/public-origins";
export const { appOrigin, marketingOrigin } = publicOrigins(process.env);
