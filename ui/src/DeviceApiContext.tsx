import { createContext, useContext } from "react";
import { api } from "./api";

export const DeviceApiContext = createContext(api);
export const useDeviceApi = () => useContext(DeviceApiContext);
