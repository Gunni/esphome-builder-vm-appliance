# Bash completion delegates to the installed systemd completion functions.
_esphome_complete() {
    local helper=${COMP_WORDS[0]} command verb prefix
    local -a COMP_WORDS=("${COMP_WORDS[@]}")
    local COMP_CWORD=$COMP_CWORD
    local COMP_LINE=${COMP_LINE-} COMP_POINT=${COMP_POINT:-0}
    case $helper in
        esphome-logs)
            command=journalctl
            COMP_WORDS[0]=$command
            prefix=$command
            ;;
        *)
            command=systemctl
            verb=${helper#esphome-}
            local -a units=(esphome-builder.service)
            case $verb in
                update) verb=start; units=(podman-auto-update.service) ;;
                timers) verb=list-timers; units=(podman-auto-update.timer esphome-image-clean.timer) ;;
            esac
            # Complete flags and their values, not additional units from the admin's manager.
            [[ ${COMP_WORDS[COMP_CWORD]} == -* || ${COMP_WORDS[COMP_CWORD-1]} == -* ]] || return 0
            COMP_WORDS=(systemctl --user "$verb" "${units[@]}" "${COMP_WORDS[@]:1}")
            ((COMP_CWORD += 2 + ${#units[@]}))
            prefix="systemctl --user $verb ${units[*]}"
            ;;
    esac
    if [[ -n $COMP_LINE ]]; then
        COMP_LINE="$prefix${COMP_LINE:${#helper}}"
        ((COMP_POINT += ${#prefix} - ${#helper}))
    fi
    # The lazy loader may return 124 even after successfully loading a function.
    declare -F "_$command" >/dev/null || _completion_loader "$command" || :
    declare -F "_$command" >/dev/null || return 0
    "_$command"
}
complete -F _esphome_complete esphome-logs esphome-start esphome-stop esphome-restart esphome-status esphome-update esphome-timers
