package com.personalmemory.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.concurrent.*;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;

/** Official delegated Microsoft OAuth. Never reads OneDrive app credentials. */
final class OneDriveSync {
    interface LoginCallback { void status(String message, String userCode, boolean complete); }
    private static final String SCOPES = "https://graph.microsoft.com/Files.ReadWrite offline_access";
    private static final String KEY = "personal-memory-onedrive";
    private static final ScheduledExecutorService AUTH = Executors.newSingleThreadScheduledExecutor();
    private static volatile String accessToken = "";
    private static volatile long accessExpiry;
    private static volatile long loginGeneration;
    private static volatile String folderId = "";
    private static final Object AUTH_STATE = new Object();
    private OneDriveSync() {}
    static boolean enabled(Context c) { return MemoryClient.preferences(c).getBoolean("onedrive_enabled", false); }
    static boolean connected(Context c) { return !MemoryClient.preferences(c).getString("onedrive_refresh", "").isEmpty(); }
    static void disconnect(Context c) {
        synchronized(AUTH_STATE) {
            loginGeneration++; accessToken=""; accessExpiry=0; folderId="";
            MemoryClient.preferences(c).edit().remove("onedrive_refresh").putBoolean("onedrive_enabled",false).apply();
        }
    }
    private static String client(Context c) throws IOException {
        String value=MemoryClient.preferences(c).getString("onedrive_client_id", "").trim();
        if(!value.matches("[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}"))
            throw new IOException("请先填写 Personal Memory 的 Microsoft 应用 Client ID");
        return value;
    }
    private static String authority(Context c) throws IOException {
        String tenant=MemoryClient.preferences(c).getString("onedrive_tenant", "organizations").trim();
        if(!tenant.matches("[a-zA-Z0-9.-]{1,128}")) throw new IOException("Microsoft 租户格式不正确");
        return "https://login.microsoftonline.com/"+tenant+"/oauth2/v2.0/";
    }
    static void login(Context context, LoginCallback callback) {
        Context c=context.getApplicationContext(); final long generation;
        synchronized(AUTH_STATE) { generation=++loginGeneration; }
        AUTH.execute(() -> {
            try {
                JSONObject response=form(authority(c)+"devicecode", "client_id="+encode(client(c))+"&scope="+encode(SCOPES));
                if(generation!=loginGeneration) return;
                String code=response.getString("user_code");
                callback.status("请在微软官方 microsoft.com/devicelogin 页面输入下方代码并授权 Personal Memory",code,false);
                long deadline=System.currentTimeMillis()+Math.min(900,response.getLong("expires_in"))*1000;
                poll(c,response.getString("device_code"),code,Math.max(5,response.optInt("interval",5)),deadline,generation,callback);
            } catch(Exception e) { callback.status(message(e),"",false); }
        });
    }
    private static void poll(Context c,String deviceCode,String code,int interval,long deadline,long generation,LoginCallback callback) {
        AUTH.schedule(() -> {
            if(generation!=loginGeneration) return;
            if(System.currentTimeMillis()>deadline) { callback.status("微软授权已过期，请重新连接", "",false); return; }
            try {
                JSONObject response=form(authority(c)+"token","client_id="+encode(client(c))
                    +"&grant_type=urn:ietf:params:oauth:grant-type:device_code&device_code="+encode(deviceCode));
                synchronized(AUTH_STATE) {
                    if(generation!=loginGeneration) return;
                    saveToken(c,response,generation); folderId="";
                    MemoryClient.preferences(c).edit().putBoolean("onedrive_enabled",true).apply();
                }
                callback.status("Microsoft 已授权，正在验证目标文件夹并同步", "",true);
                MemoryClient.flush(c,null);
            } catch(ApiError e) {
                if("authorization_pending".equals(e.code) || "slow_down".equals(e.code)) {
                    poll(c,deviceCode,code,"slow_down".equals(e.code)?interval+5:interval,deadline,generation,callback);
                } else callback.status(message(e),"",false);
            } catch(Exception e) { callback.status(message(e),"",false); }
        },interval,TimeUnit.SECONDS);
    }
    private static synchronized String token(Context c) throws Exception {
        long generation=loginGeneration;
        if(!accessToken.isEmpty() && System.currentTimeMillis()<accessExpiry) return accessToken;
        String refresh=MemoryClient.preferences(c).getString("onedrive_refresh", "");
        if(refresh.isEmpty()) throw new IOException("OneDrive 直传尚未授权，请连接 Microsoft");
        JSONObject response=form(authority(c)+"token","client_id="+encode(client(c))+"&grant_type=refresh_token&refresh_token="
            +encode(unprotect(refresh))+"&scope="+encode(SCOPES));
        saveToken(c,response,generation); return accessToken;
    }
    private static void saveToken(Context c,JSONObject response,long generation) throws Exception {
        synchronized(AUTH_STATE) {
            if(generation!=loginGeneration) throw new IOException("授权操作已取消");
            String fresh=response.getString("access_token");
            if(response.has("refresh_token")) {
                if(!MemoryClient.preferences(c).edit().putString("onedrive_refresh",protect(response.getString("refresh_token"))).commit())
                    throw new IOException("本机授权保存失败");
            }
            accessToken=fresh; accessExpiry=System.currentTimeMillis()+Math.max(0,response.optLong("expires_in",3600)-60)*1000;
        }
    }
    private static SecretKey key() throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if(store.containsAlias(KEY)) return (SecretKey)store.getKey(KEY,null);
        KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }
    private static String protect(String value) throws Exception {
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE,key());
        return Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP)+":"+Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);
    }
    private static String unprotect(String value) throws Exception {
        String[] parts=value.split(":",2); Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(parts[0],Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(parts[1],Base64.NO_WRAP)),StandardCharsets.UTF_8);
    }
    private static String folder(Context c) throws IOException {
        String value=MemoryClient.preferences(c).getString("onedrive_folder", "Personal Memory/data").trim();
        if(value.isEmpty() || value.length()>500 || value.startsWith("/") || value.endsWith("/")
            || value.contains("\\") || value.contains("//") || value.matches(".*(^|/)\\.\\.?(/|$).*"))
            throw new IOException("请检查 OneDrive 目标文件夹路径");
        return value;
    }
    static void health(Context c) throws Exception { graph(c,"GET","/me/drive/root:/"+path(folder(c)),null); }
    static void upload(Context c,JSONArray batch) throws Exception {
        StringBuilder lines=new StringBuilder();
        for(int i=0;i<batch.length();i++) lines.append(batch.getJSONObject(i).toString()).append('\n');
        byte[] bytes=lines.toString().getBytes(StandardCharsets.UTF_8);
        if(bytes.length>300000) throw new IOException("OneDrive 批次超出上限");
        String parent=inbox(c);
        String name="android-"+CapturePrivacy.digest(lines.toString())+".jsonl";
        JSONObject ack=graph(c,"PUT","/me/drive/items/"+encodePath(parent)+":/"+name+":/content",bytes);
        if(ack.optLong("size",-1)!=bytes.length || !name.equals(ack.optString("name")) || ack.optString("id").isEmpty())
            throw new IOException("OneDrive 未完整确认上传，记录仍保留在手机");
        MemoryClient.preferences(c).edit().putLong("onedrive_last_upload",System.currentTimeMillis()).apply();
    }
    private static synchronized String inbox(Context c) throws Exception {
        if(!folderId.isEmpty()) return folderId;
        JSONObject base=graph(c,"GET","/me/drive/root:/"+path(folder(c)),null);
        if(!base.has("folder")) throw new IOException("OneDrive 目标不是文件夹");
        String parent=base.getString("id");
        String current=folder(c);
        for(String child:new String[]{"inbox","android"}) {
            current+="/"+child;
            JSONObject found;
            try { found=graph(c,"GET","/me/drive/root:/"+path(current),null); }
            catch(ApiError e) {
                if(e.status!=404) throw e;
                JSONObject body=new JSONObject().put("name",child).put("folder",new JSONObject()).put("@microsoft.graph.conflictBehavior","fail");
                try { found=graph(c,"POST","/me/drive/items/"+encodePath(parent)+"/children",body.toString().getBytes(StandardCharsets.UTF_8)); }
                catch(ApiError conflict) { if(conflict.status!=409) throw conflict; found=graph(c,"GET","/me/drive/root:/"+path(current),null); }
            }
            if(!found.has("folder")) throw new IOException("OneDrive 收件目录冲突");
            parent=found.getString("id");
        }
        folderId=parent; return parent;
    }
    private static JSONObject graph(Context c,String method,String route,byte[] body) throws Exception {
        return request("https://graph.microsoft.com/v1.0"+route,method,body,token(c),method.equals("PUT")?"text/plain; charset=utf-8":"application/json");
    }
    private static JSONObject form(String url,String body) throws Exception {
        return request(url,"POST",body.getBytes(StandardCharsets.UTF_8),null,"application/x-www-form-urlencoded");
    }
    private static JSONObject request(String url,String method,byte[] body,String bearer,String type) throws Exception {
        HttpURLConnection connection=(HttpURLConnection)new URL(url).openConnection();
        try {
            connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(10000); connection.setReadTimeout(10000);
            connection.setRequestMethod(method); connection.setRequestProperty("Content-Type",type);
            if(bearer!=null) connection.setRequestProperty("Authorization","Bearer "+bearer);
            if(body!=null) { connection.setDoOutput(true); connection.setFixedLengthStreamingMode(body.length); try(OutputStream stream=connection.getOutputStream()){stream.write(body);} }
            int status=connection.getResponseCode(); InputStream stream=status<400?connection.getInputStream():connection.getErrorStream();
            StringBuilder result=new StringBuilder();
            if(stream!=null) try(BufferedReader reader=new BufferedReader(new InputStreamReader(stream,StandardCharsets.UTF_8))){
                String line; while((line=reader.readLine())!=null){result.append(line);if(result.length()>600000)throw new IOException("响应过大");}
            }
            JSONObject json=result.length()==0?new JSONObject():new JSONObject(result.toString());
            if(status<200 || status>=300) {
                Object error=json.opt("error"); String code=error instanceof JSONObject?((JSONObject)error).optString("code"):json.optString("error");
                if(status==401) { accessToken=""; accessExpiry=0; }
                throw new ApiError(status,code);
            }
            return json;
        } finally { connection.disconnect(); }
    }
    static String message(Exception e) {
        if(e instanceof ApiError) {
            ApiError error=(ApiError)e;
            if("invalid_client".equals(error.code) || "unauthorized_client".equals(error.code)) return "Microsoft 应用注册未完成，请检查 Client ID 和公共客户端设置";
            if(error.status==401 || "invalid_grant".equals(error.code)) return "Microsoft 授权需要重新登录";
            if(error.status==403 || "access_denied".equals(error.code)) return "Microsoft 拒绝授权，可能需要组织管理员批准";
            if(error.status==404) return "OneDrive 中未找到配置的目标文件夹，请确认账户和路径";
            return "Microsoft 请求暂未成功（HTTP "+error.status+"），记录仍保留在手机";
        }
        if(e instanceof IOException && e.getMessage()!=null && !e.getMessage().matches(".*[A-Za-z]{10,}.*")) return e.getMessage();
        return "OneDrive 暂不可达或配置未完成，记录保留待重试";
    }
    private static String encode(String s)throws Exception{return URLEncoder.encode(s,"UTF-8");}
    private static String encodePath(String s){return android.net.Uri.encode(s);}
    private static String path(String s){StringBuilder b=new StringBuilder();for(String p:s.split("/")){if(b.length()>0)b.append('/');b.append(encodePath(p));}return b.toString();}
    private static final class ApiError extends IOException {
        final int status; final String code;
        ApiError(int status,String code){super("Microsoft HTTP "+status);this.status=status;this.code=code;}
    }
}
