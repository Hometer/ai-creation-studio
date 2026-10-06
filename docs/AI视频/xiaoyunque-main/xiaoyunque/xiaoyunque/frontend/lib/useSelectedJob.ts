'use client';
import {useCallback,useEffect,useSyncExternalStore} from 'react';
import {useSearchParams} from 'next/navigation';
const event='native-job-selection';
function subscribe(callback:()=>void){window.addEventListener('popstate',callback);window.addEventListener(event,callback);return()=>{window.removeEventListener('popstate',callback);window.removeEventListener(event,callback);};}
export function useSelectedJob(parameter='job'){
 const navigation=useSearchParams()?.toString()??'';
 useEffect(()=>{window.dispatchEvent(new Event(event));},[navigation]);
 const read=useCallback(()=>{const value=new URLSearchParams(window.location.search).get(parameter)??'';return /^[a-f0-9]{32}$/.test(value)?value:'';},[parameter]);
 const selected=useSyncExternalStore(subscribe,read,()=> '');
 const set=useCallback((value:string)=>{if(value&&!/^[a-f0-9]{32}$/.test(value))throw Error('无效任务编号');const url=new URL(location.href);if(value)url.searchParams.set(parameter,value);else url.searchParams.delete(parameter);history.replaceState(null,'',url);window.dispatchEvent(new Event(event));},[parameter]);
 return [selected,set] as const;
}
