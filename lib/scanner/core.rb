module Yawast
  module Scanner
    class Core
      def self.process(uri, ssl_test)
        Yawast.header
        puts "Scanning: #{uri.to_s}"
        puts ''

        begin
          server_info(uri)

          #perfom SSL checks
          if (uri.scheme == 'https') && ssl_test
            Yawast::Scanner::Ssl.info(uri)
            Yawast::Scanner::Ssl.check_hsts(uri)
          end

          #apache specific checks
          Yawast::Scanner::Apache.check_server_status(uri)
          Yawast::Scanner::Apache.check_server_info(uri)

          #iis specific checks
          Yawast::Scanner::Iis.check_asp_banner(uri)
        rescue => e
          Yawast::Utilities.puts_error "Fatal Error: Can not continue. (#{e.message})"
        end
      end

      def self.server_info(uri)
        begin
          Yawast::Utilities.puts_info "Full URI: #{uri}"

          Yawast::Utilities.puts_info 'IP(s):'
          dns = Resolv::DNS.new()
          addr = dns.getaddresses(uri.host)
          addr.each do |ip|
            begin
              host_name = dns.getname(ip.to_s)
            rescue
              host_name = 'N/A'
            end

            Yawast::Utilities.puts_info "\t\t#{ip} (#{host_name})"
          end
          puts ''

          server = ''
          powered_by = ''
          head = Yawast::Scanner::Http.head(uri)
          Yawast::Utilities.puts_info 'HEAD:'
          head.each do |k, v|
            Yawast::Utilities.puts_info "\t\t#{k}: #{v}"

            server = v if k.downcase == 'server'
            powered_by = v if k.downcase == 'x-powered-by'
          end
          puts ''

          if server != ''
            Yawast::Scanner::Apache.check_banner(server)
            Yawast::Scanner::Php.check_banner(server)
            Yawast::Scanner::Iis.check_banner(server)
            Yawast::Scanner::Nginx.check_banner(server)
          end

          if powered_by != ''
            Yawast::Utilities.puts_warn "X-Powered-By Header Present: #{powered_by}"
            puts ''
          end
        rescue => e
          Yawast::Utilities.puts_error "Error getting basic information: #{e.message}"
          raise
        end
      end
    end
  end
end
